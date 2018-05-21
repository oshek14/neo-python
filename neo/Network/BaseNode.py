import binascii
import random
from logzero import logger
from twisted.internet.protocol import Protocol
from twisted.internet import reactor, task
from neo.Core.Blockchain import Blockchain as BC
from neocore.IO.BinaryReader import BinaryReader
from neo.Network.Message import Message
from neo.IO.MemoryStream import StreamManager
from neo.IO.Helper import Helper as IOHelper
from neo.Core.Helper import Helper
from .Payloads.GetBlocksPayload import GetBlocksPayload
from .Payloads.InvPayload import InvPayload
from .Payloads.NetworkAddressWithTime import NetworkAddressWithTime
from .Payloads.VersionPayload import VersionPayload
from .InventoryType import InventoryType
from neo.Settings import settings

import traceback


class BaseNode(Protocol):
    Version = None

    leader = None

    header_loop = None

    identifier = None

    def __init__(self):
        """
        Create an instance.
        The NeoNode class is the equivalent of the C# RemoteNode.cs class. It represents a single Node connected to the client.
        """
        from neo.Network.NodeLeader import NodeLeader

        self.leader = NodeLeader.Instance()
        self.nodeid = self.leader.NodeId
        self.remote_nodeid = random.randint(1294967200, 4294967200)
        self.endpoint = ''
        self.buffer_in = bytearray()
        self.myblockrequests = set()
        self.bytes_in = 0
        self.bytes_out = 0

        self.host = None
        self.port = None
        self.identifier = self.leader.NodeCount
        self.leader.NodeCount += 1

        self.Log("New Node created %s " % self.identifier)

    def Disconnect(self):
        """Close the connection with the remote node client."""
        self.transport.loseConnection()

    @property
    def Address(self):
        if self.endpoint:
            return "%s:%s" % (self.endpoint.host, self.endpoint.port)
        return ""

    def Name(self):
        """
        Get the peer name.

        Returns:
            str:
        """
        return self.transport.getPeer()

    def GetNetworkAddressWithTime(self):
        """
        Get a network address object.

        Returns:
            NetworkAddressWithTime: if we have a connection to a node.
            None: otherwise.
        """
        if self.port is not None and self.host is not None:
            return NetworkAddressWithTime(self.host, self.port, self.Version.Services)
        return None

    def IOStats(self):
        """
        Get the connection I/O stats.

        Returns:
            str:
        """
        biM = self.bytes_in / 1000000  # megabyes
        boM = self.bytes_out / 1000000

        return "%s MB in / %s MB out" % (biM, boM)

    def connectionMade(self):
        """Callback handler from twisted when establishing a new connection."""
        self.endpoint = self.transport.getPeer()
        self.host = self.endpoint.host
        self.port = int(self.endpoint.port)
        self.leader.AddConnectedPeer(self)
        self.Log("Connection from %s" % self.endpoint)

    def connectionLost(self, reason=None):
        """Callback handler from twisted when a connection was lost."""
        if self.header_loop:
            self.header_loop.stop()
            self.header_loop = None

        self.ReleaseBlockRequests()
        self.leader.RemoveConnectedPeer(self)
        self.Log("%s disconnected %s" % (self.remote_nodeid, reason))

    def ReleaseBlockRequests(self):

        self.myblockrequests = set()

    def dataReceived(self, data):
        """ Called from Twisted whenever data is received. """
        self.bytes_in += (len(data))
        self.buffer_in = self.buffer_in + data
        self.CheckDataReceived()

    def CheckDataReceived(self):
        """Tries to extract a Message from the data buffer and process it."""
        currentLength = len(self.buffer_in)
        if currentLength < 24:
            return

        # Extract the message header from the buffer, and return if not enough
        # buffer to fully deserialize the message object.
        try:
            # Construct message
            mstart = self.buffer_in[:24]
            ms = StreamManager.GetStream(mstart)
            reader = BinaryReader(ms)
            m = Message()

            # Extract message metadata
            m.Magic = reader.ReadUInt32()
            m.Command = reader.ReadFixedString(12).decode('utf-8')
            m.Length = reader.ReadUInt32()
            m.Checksum = reader.ReadUInt32()

            # Return if not enough buffer to fully deserialize object.
            messageExpectedLength = 24 + m.Length
            if currentLength < messageExpectedLength:
                return

        except Exception as e:
            self.Log('Error: Could not read initial bytes %s ' % e)
            return

        finally:
            StreamManager.ReleaseStream(ms)
            del reader

        # The message header was successfully extracted, and we have enough enough buffer
        # to extract the full payload
        orig_buffer_in = self.buffer_in
        try:
            # Extract message bytes from buffer and truncate buffer
            mdata = self.buffer_in[:messageExpectedLength]
            self.buffer_in = self.buffer_in[messageExpectedLength:]

            # Deserialize message with payload
            stream = StreamManager.GetStream(mdata)
            reader = BinaryReader(stream)
            message = Message()
            message.Deserialize(reader)

            # Propagate new message
            self.MessageReceived(message)

        except Exception as e:
            traceback.print_stack()
            traceback.print_exc()

            self.Log('Error: Could not extract message: %s ' % e)
            self.buffer_in = orig_buffer_in
            return

        finally:
            StreamManager.ReleaseStream(stream)

        # Finally, after a message has been fully deserialized and propagated,
        # check if another message can be extracted with the current buffer:
        if len(self.buffer_in) >= 24:
            self.CheckDataReceived()

    def MessageReceived(self, m: Message):
        """
        Process a message.

        Args:
            m (neo.Network.Message):
        """

        if m.Command == 'verack':
            self.HandleVerack()
        elif m.Command == 'version':
            self.HandleVersion(m.Payload)
        elif m.Command == 'getaddr':
            self.SendPeerInfo()
        elif m.Command == 'getdata':
            self.HandleGetDataMessageReceived(m.Payload)
        elif m.Command == 'getblocks':
            self.HandleGetBlocksMessageReceived(m.Payload)
        elif m.Command == 'inv':
            self.HandleInvMessage(m.Payload)
        elif m.Command == 'block':
            self.HandleBlockReceived(m.Payload)
        elif m.Command == 'headers':
            self.HandleBlockHeadersReceived(m.Payload)
        elif m.Command == 'addr':
            self.HandlePeerInfoReceived(m.Payload)
        else:
            self.Log("Command %s not implemented " % m.Command)

    def ProtocolReady(self):

        self.header_loop = task.LoopingCall(self.AskForMoreHeaders)
        self.header_loop.start(10, now=True)

    def AskForMoreHeaders(self):
        # self.Log("asking for more headers...")
        get_headers_message = Message("getheaders", GetBlocksPayload(hash_start=[BC.Default().CurrentHeaderHash]))
        self.SendSerializedMessage(get_headers_message)

    def AskForMoreBlocks(self, offset, page, num_to_request, hashes=None):
        pass

    def HandlePeerInfoReceived(self, payload):
        """Process response of `self.RequestPeerInfo`."""
        pass

    def SendPeerInfo(self):
        pass

    def RequestVersion(self):
        """Request the remote client version."""
        # self.Log("All caught up, requesting version")
        m = Message("getversion")
        self.SendSerializedMessage(m)

    def SendVersion(self):
        """Send our client version."""
        m = Message("version", VersionPayload(settings.NODE_PORT, self.remote_nodeid, settings.VERSION_NAME))
        self.SendSerializedMessage(m)

    def HandleVersion(self, payload):
        """Process the response of `self.RequestVersion`."""
        self.Version = IOHelper.AsSerializableWithType(payload, "neo.Network.Payloads.VersionPayload.VersionPayload")
        self.nodeid = self.Version.Nonce
#        self.Log("Remote version %s " % vars(self.Version))
        self.SendVersion()

    def HandleVerack(self):
        """Handle the `verack` response."""
        m = Message('verack')
        self.SendSerializedMessage(m)
        self.ProtocolReady()

    def HandleInvMessage(self, payload):
        """
        Process a block header inventory payload.

        Args:
            inventory (neo.Network.Payloads.InvPayload):
        """
        pass

    def SendSerializedMessage(self, message: Message):
        """
        Send the `message` to the remote client.

        Args:
            message (neo.Network.Message):
        """
        ba = Helper.ToArray(message)
        ba2 = binascii.unhexlify(ba)
        self.bytes_out += len(ba2)
        self.transport.write(ba2)

    def HandleBlockHeadersReceived(self, inventory):
        """
        Process a block header inventory payload.

        Args:
            inventory (neo.Network.Inventory):
        """
        inventory = IOHelper.AsSerializableWithType(inventory, 'neo.Network.Payloads.HeadersPayload.HeadersPayload')
        if inventory is not None:
            BC.Default().AddHeaders(inventory.Headers)

    def HandleBlockReceived(self, inventory):
        """
        Process a Block inventory payload.

        Args:
            inventory (neo.Network.Inventory):
        """
        pass

    def HandleGetDataMessageReceived(self, payload):
        """
        Process a InvPayload payload.

        Args:
            payload (neo.Network.Inventory):
        """
        inventory = IOHelper.AsSerializableWithType(payload, 'neo.Network.Payloads.InvPayload.InvPayload')

        for hash in inventory.Hashes:
            hash = hash.encode('utf-8')

            item = None
            # try to get the inventory to send from relay cache

            if hash in self.leader.RelayCache.keys():
                item = self.leader.RelayCache[hash]

            if inventory.Type == InventoryType.TXInt:
                if not item:
                    item, index = BC.Default().GetTransaction(hash)
                if not item:
                    item = self.leader.GetTransaction(hash)
                if item:
                    message = Message(command='tx', payload=item, print_payload=False)
                    self.SendSerializedMessage(message)

            elif inventory.Type == InventoryType.BlockInt:
                if not item:
                    item = BC.Default().GetBlock(hash)
                if item:
                    message = Message(command='block', payload=item, print_payload=True)
                    self.SendSerializedMessage(message)

            elif inventory.Type == InventoryType.ConsensusInt:
                if item:
                    self.SendSerializedMessage(Message(command='consensus', payload=item))

    def HandleGetBlocksMessageReceived(self, payload):
        """
        Process a GetBlocksPayload payload.

        Args:
            payload (neo.Network.Payloads.GetBlocksPayload):
        """

        pass

    def Relay(self, inventory):
        """
        Wrap the inventory in a InvPayload object and send it over the write to the remote node.

        Args:
            inventory:

        Returns:
            bool: True (fixed)
        """
        inventory = InvPayload(type=inventory.InventoryType, hashes=[inventory.Hash.ToBytes()])
        m = Message("inv", inventory)
        self.SendSerializedMessage(m)

        return True

    def Log(self, msg):
        logger.debug("[%s] %s - %s" % (self.identifier, self.endpoint, msg))
