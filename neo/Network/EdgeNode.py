from neo.Core.Blockchain import Blockchain as BC
from neo.Network.Message import Message
from neo.IO.Helper import Helper as IOHelper
from neo.Network.Payloads.InvPayload import InvPayload
from neo.Network.InventoryType import InventoryType
from neo.Network.BaseNode import BaseNode
from twisted.internet import reactor


class EdgeNode(BaseNode):

    def connectionMade(self):
        """Callback handler from twisted when establishing a new connection."""
        super(EdgeNode, self).connectionMade()

    def connectionLost(self, reason=None):
        """Callback handler from twisted when a connection was lost."""
        super(EdgeNode, self).connectionLost(reason)

    def AskForMoreBlocks(self, offset, page, num_to_request, hashes=None):

        if not hashes:
            hashes = []
            hashstart = offset + (page * num_to_request)
            current_header_height = BC.Default().HeaderHeight + 1

            while hashstart < current_header_height and len(hashes) < num_to_request:
                hash = BC.Default().GetHeaderHash(hashstart)
                hashes.append(hash)

                hashstart += 1

        if len(hashes) > 0:
            #            self.Log("asked for more blocks ... %s thru %s (%s blocks) " % (first, hashstart, len(hashes)))
            message = Message("getdata", InvPayload(InventoryType.Block, hashes))
            self.SendSerializedMessage(message)

    def HandleInvMessage(self, payload):
        """
        Process a block header inventory payload.

        Args:
            inventory (neo.Network.Payloads.InvPayload):
        """

        inventory = IOHelper.AsSerializableWithType(payload, 'neo.Network.Payloads.InvPayload.InvPayload')

        if inventory.Type == InventoryType.BlockInt:

            ok_hashes = []
            for hash in inventory.Hashes:
                hash = hash.encode('utf-8')
                if hash not in self.myblockrequests:
                    ok_hashes.append(hash)
                    self.myblockrequests.add(hash)
            if len(ok_hashes):
                #                logger.info("OK HASHES, get data %s " % ok_hashes)
                message = Message("getdata", InvPayload(InventoryType.Block, ok_hashes))
                self.SendSerializedMessage(message)

        elif inventory.Type == InventoryType.TXInt:
            pass
        elif inventory.Type == InventoryType.ConsensusInt:
            pass

    def HandleBlockReceived(self, inventory):
        """
        Process a Block inventory payload.

        Args:
            inventory (neo.Network.Inventory):
        """
        block = IOHelper.AsSerializableWithType(inventory, 'neo.Core.Block.Block')

        blockhash = block.Hash.ToBytes()
        if blockhash in self.myblockrequests:
            self.myblockrequests.remove(blockhash)

        self.leader.InventoryReceived(block)

    def HandleGetBlocksMessageReceived(self, payload):
        """
        Process a GetBlocksPayload payload.

        Args:
            payload (neo.Network.Payloads.GetBlocksPayload):
        """
        if not self.leader.ServiceEnabled:
            return

        inventory = IOHelper.AsSerializableWithType(payload, 'neo.Network.Payloads.GetBlocksPayload.GetBlocksPayload')

        if not BC.Default().GetHeader(inventory.HashStart):
            self.Log("Hash %s not found " % inventory.HashStart)
            return

        hashes = []
        hcount = 0
        hash = inventory.HashStart
        while hash != inventory.HashStop and hcount < 500:
            hash = BC.Default().GetNextBlockHash(hash)
            if hash is None:
                break
            hashes.append(hash)
            hcount += 1
        if hcount > 0:
            self.Log("sending inv hashes! %s " % hashes)
            self.SendSerializedMessage(Message('inv', InvPayload(type=InventoryType.Block, hashes=hashes)))
