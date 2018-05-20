import random
from logzero import logger
from neo.Core.Block import Block
from neo.Core.Blockchain import Blockchain as BC
from neo.Implementations.Blockchains.LevelDB.TestLevelDBBlockchain import TestLevelDBBlockchain
from neo.Core.TX.Transaction import Transaction
from neo.Core.TX.MinerTransaction import MinerTransaction
from neo.Network.EdgeNode import EdgeNode
from neo.Network.PeeringEdgeNode import PeeringEdgeNode
from neo.Settings import settings
from twisted.internet.protocol import ReconnectingClientFactory
from twisted.internet import reactor, task
import time
import random


class BaseClientFactory(ReconnectingClientFactory):
    protocol = EdgeNode
    maxRetries = 1

    def clientConnectionFailed(self, connector, reason):
        address = "%s:%s" % (connector.host, connector.port)
        logger.debug("Dropped connection from %s " % address)
        for peer in NodeLeader.Instance().Peers:
            if peer.Address == address:
                peer.connectionLost()


class PeeringClientFactory(BaseClientFactory):
    protocol = PeeringEdgeNode


EDGE_NODE_LOOP = 3
EDGE_NODE_REQ_SIZE = 40
EDGE_NODE_MHASH_SIZE = 20
MAX_CACHE_SIZE = 10000
RESET_HEADER_COUNT = 20


class NodeLeader:
    __LEAD = None

    Peers = []

    UnconnectedPeers = []

    ADDRS = []

    NodeId = None

    KnownHashes = []
    MissionsGlobal = []
    MemPool = {}
    RelayCache = {}

    NodeCount = 0

    ServiceEnabled = False

    block_loop = None

    reset_count = 0
    reset_blockheight = 0

    @staticmethod
    def Instance():
        """
        Get the local node instance.

        Returns:
            NodeLeader: instance.
        """
        if NodeLeader.__LEAD is None:
            NodeLeader.__LEAD = NodeLeader()
        return NodeLeader.__LEAD

    def __init__(self):
        """
        Create an instance.
        This is the equivalent to C#'s LocalNode.cs
        """
        self.Setup()
        self.ServiceEnabled = settings.SERVICE_ENABLED

    def Setup(self):
        """
        Initialize the local node.

        Returns:

        """
        self.Peers = []
        self.UnconnectedPeers = []
        self.ADDRS = []
        self.MissionsGlobal = []
        self.NodeId = random.randint(1294967200, 4294967200)

    def Restart(self):
        if len(self.Peers) == 0:
            self.ADDRS = []
            self.Start()

    def OnBlockLoop(self):

        start = time.time()

        BC.Default().PersistBlocks()

        elapsed = time.time() - start

        if elapsed > EDGE_NODE_LOOP:
            return

        bcache = BC.Default()._block_cache
        bclen = len(bcache)

        if bclen > MAX_CACHE_SIZE:
            BC.Default()._block_cache = {}
            bclen = 0
            if BC.Default().Height == self.reset_blockheight:
                logger.info("Block cache has been reset at same block %s times" % self.reset_count)
                self.reset_count += 1
            else:
                self.reset_count = 0

            self.reset_blockheight = BC.Default().Height

            if self.reset_count > RESET_HEADER_COUNT:
                BC.Default().ResetHeadersAfter(self.reset_blockheight)
                self.reset_count = 0
                return

        current = BC.Default().Height

        start_hash_height = BC.Default().Height + 1
        count = 0
        missing_hashes = []

        while count < bclen:
            target_hash = BC.Default().GetHeaderHash(start_hash_height + count)
            if target_hash not in bcache.keys():
                missing_hashes.append(target_hash)
            count += 1

        startoffset = current + bclen
        count = 0

        plist = self.Peers
        random.shuffle(plist)

        for peer in plist:
            if len(missing_hashes):
                to_request = missing_hashes[0:EDGE_NODE_MHASH_SIZE]
                del missing_hashes[0:EDGE_NODE_MHASH_SIZE]
                if None not in to_request:
                    peer.AskForMoreBlocks(startoffset, count, EDGE_NODE_REQ_SIZE, hashes=to_request)
            else:
                peer.AskForMoreBlocks(startoffset, count, EDGE_NODE_REQ_SIZE)
                count += 1

    def Start(self):
        """Start connecting to the node list."""
        # start up endpoints

        self.block_loop = task.LoopingCall(self.OnBlockLoop)
        self.block_loop.start(EDGE_NODE_LOOP)

        for bootstrap in settings.SEED_LIST:
            host, port = bootstrap.split(":")
            self.ADDRS.append('%s:%s' % (host, port))
            self.SetupConnection(host, port)

    def RemoteNodePeerReceived(self, host, port, index):
        addr = '%s:%s' % (host, port)
        if addr not in self.ADDRS and len(self.Peers) < settings.CONNECTED_PEER_MAX:
            self.ADDRS.append(addr)
            self.SetupConnection(host, port)

    def SetupConnection(self, host, port):
        if len(self.Peers) < settings.CONNECTED_PEER_MAX:
            reactor.connectTCP(host, int(port), PeeringClientFactory())

    def Shutdown(self):
        """Disconnect all connected peers."""
        self.block_loop.stop()

        for p in self.Peers:
            p.Disconnect()

    def AddConnectedPeer(self, peer):
        """
        Add a new connect peer to the known peers list.

        Args:
            peer (NeoNode): instance.
        """

        if peer not in self.Peers:
            if len(self.Peers) < settings.CONNECTED_PEER_MAX:
                self.Peers.append(peer)
            else:
                if peer.Address in self.ADDRS:
                    self.ADDRS.remove(peer.Address)
                peer.Disconnect()

    def RemoveConnectedPeer(self, peer):
        """
        Remove a connected peer from the known peers list.

        Args:
            peer (NeoNode): instance.
        """
        if peer in self.Peers:
            self.Peers.remove(peer)
        if peer.Address in self.ADDRS:
            self.ADDRS.remove(peer.Address)
        if len(self.Peers) == 0:
            reactor.callLater(10, self.Restart)

    def InventoryReceived(self, inventory):
        """
        Process a received inventory.

        Args:
            inventory (neo.Network.Inventory): expect a Block type.

        Returns:
            bool: True if processed and verified. False otherwise.
        """

        if inventory is MinerTransaction:
            return False

        if type(inventory) is Block:
            if BC.Default() is None:
                return False

            if BC.Default().ContainsBlock(inventory.Index):
                return False

            if not BC.Default().AddBlock(inventory):
                return False

        else:
            if not inventory.Verify(self.MemPool.values()):
                return False

    def RelayDirectly(self, inventory):
        """
        Relay the inventory to the remote client.

        Args:
            inventory (neo.Network.Inventory):

        Returns:
            bool: True if relayed successfully. False otherwise.
        """
        relayed = False

        self.RelayCache[inventory.Hash.ToBytes()] = inventory

        for peer in self.Peers:
            relayed |= peer.Relay(inventory)

        if len(self.Peers) == 0:
            if type(BC.Default()) is TestLevelDBBlockchain:
                # mock a true result for tests
                return True

            logger.info("no connected peers")

        return relayed

    def Relay(self, inventory):
        """
        Relay the inventory to the remote client.

        Args:
            inventory (neo.Network.Inventory):

        Returns:
            bool: True if relayed successfully. False otherwise.
        """
        if type(inventory) is MinerTransaction:
            return False

        if inventory.Hash.ToBytes() in self.KnownHashes:
            return False

        self.KnownHashes.append(inventory.Hash.ToBytes())

        if type(inventory) is Block:
            pass

        elif type(inventory) is Transaction or issubclass(type(inventory), Transaction):
            if not self.AddTransaction(inventory):
                return False
        else:
            # consensus
            pass

        relayed = self.RelayDirectly(inventory)
        # self.
        return relayed

    def GetTransaction(self, hash):
        if hash in self.MemPool.keys():
            return self.MemPool[hash]
        return None

    def AddTransaction(self, tx):
        """
        Add a transaction to the memory pool.

        Args:
            tx (neo.Core.TX.Transaction): instance.

        Returns:
            bool: True if successfully added. False otherwise.
        """
        if BC.Default() is None:
            return False

        if tx.Hash.ToBytes() in self.MemPool.keys():
            return False

        if BC.Default().ContainsTransaction(tx.Hash):
            return False

        if not tx.Verify(self.MemPool.values()):
            logger.error("Veryfiying tx result... failed")
            return False

        self.MemPool[tx.Hash.ToBytes()] = tx

        return True
