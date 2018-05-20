from neo.Network.EdgeNode import EdgeNode
from neo.Network.Message import Message
from twisted.internet import task
from neo.IO.Helper import Helper as IOHelper


class PeeringEdgeNode(EdgeNode):

    peer_loop = None

    def connectionLost(self, reason=None):
        """Callback handler from twisted when a connection was lost."""

        if self.peer_loop:
            self.peer_loop.stop()
            self.peer_loop = None

        super(EdgeNode, self).connectionLost(reason)

    def ProtocolReady(self):

        super(EdgeNode, self).ProtocolReady()

        # ask every 3 minutes for new peers
        self.peer_loop = task.LoopingCall(self.RequestPeerInfo)
        self.peer_loop.start(120, now=False)

    def RequestPeerInfo(self):
        """Request the peer address information from the remote client."""
        self.SendSerializedMessage(Message('getaddr'))

    def HandlePeerInfoReceived(self, payload):
        """Process response of `self.RequestPeerInfo`."""
        addrs = IOHelper.AsSerializableWithType(payload, 'neo.Network.Payloads.AddrPayload.AddrPayload')

        for index, nawt in enumerate(addrs.NetworkAddressesWithTime):
            self.leader.RemoteNodePeerReceived(nawt.Address, nawt.Port, index)
