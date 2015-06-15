import sys
import socket
import msgpack

from twisted.internet import defer, reactor, threads
from twisted.python import log

import txthings.coap as coap
import txthings.resource as resource

from w1thermsensor import W1ThermSensor


STATUS_PS = (1 << 0)
MSGPACK_MEDIA_TYPE = 59 #number is unofficial
CONST_ETAG = "a"

#Node configuration
UUID = "19DE9D3DFF99E1596CA09B602EA744A88E340FB83D0A60A6E8403C3FDAC7F809"
TEO_ADDRESS = "com1.telemetria-online.pl"
TEO_PORT = coap.COAP_PORT

def isMainsPowered():
    """Placeholder function for status resource"""
    return STATUS_PS

class TemperatureSensor():
    """
    This class contains methods that provide temperature readouts.
    We use 3rd party module 'w1thermsensor' which might not be
    asynchronous - that's why we use deferToThread function.
    
    Temperature is updated in the background, we always use the
    latest value which might be at most (5 seconds + read time) old.
    """
    def __init__(self):
        self.sensor = W1ThermSensor()
        self.temperature = None
        reactor.callLater(0.1,self._initiateRead)

    def _initiateRead(self):
        d = threads.deferToThread(self.sensor.get_temperature)
        d.addCallback(self._processResult)

    def _processResult(self, result):
        self.temperature = result
        reactor.callLater(5, self._initiateRead)


class StatusResource (resource.CoAPResource):
    """
    Resource that represents node status. It is a mandatory resource
    for Teo application.
    """
    #isLeaf = True

    def __init__(self):
        resource.CoAPResource.__init__(self)
        self.visible = True
        self.addParam(resource.LinkParam("title", "Status resource"))

    def render_GET(self, request):
        value = 0
        value |= isMainsPowered()
        response = coap.Message(code=coap.CONTENT, payload=msgpack.packb(value))
        response.opt.content_format = MSGPACK_MEDIA_TYPE
        return defer.succeed(response)

class TemperatureResource (resource.CoAPResource):
    """Resource that represents 1-wire temperature sensor readout"""

    def __init__(self, sensor):
        resource.CoAPResource.__init__(self)
        self.visible = True
        self.addParam(resource.LinkParam("title", "Status resource"))
        self.sensor = TemperatureSensor()

    def render_GET(self, request):
        value = self.sensor.temperature
        if value is not None:
            response = coap.Message(code=coap.CONTENT, payload=msgpack.packb(value))
            response.opt.content_format = MSGPACK_MEDIA_TYPE
        else:
	    response = coap.Message(code=coap.SERVICE_UNAVAILABLE)
        return defer.succeed(response)

class Agent():
    """
    This class represents client, that registers to TEO server online
    """

    def __init__(self, protocol):
        self.protocol = protocol
        self.reg_tmr_handle = None
        self.reg_upd_address = None
        self.teo_ip_address = None
        reactor.callLater(1, self.resolveAddress)

    def resolveAddress(self):
        """
        Method for simple domain name resolution. Method uses deferToThread function, because
        socket.getaddrinfo is not asynchronous.
        """
        d = threads.deferToThread(socket.getaddrinfo, TEO_ADDRESS, TEO_PORT, 0, socket.SOCK_DGRAM)
        d.addCallback(self.register)

    def register(self, gaiResult):
        family, socktype, proto, canonname, sockaddr = gaiResult[0]
        if family in [socket.AF_INET]:
            self.teo_ip_address = sockaddr[0]
        payload = '</node/temp>;rt="temperature";if="sensor"'
        request = coap.Message(code=coap.POST, payload=payload)
        request.opt.uri_path = ("rd",)
        request.opt.uri_query = ("ep=" + UUID,)
        request.opt.content_format = coap.media_types_rev['application/link-format']
        request.opt.etag = CONST_ETAG
        request.remote = (self.teo_ip_address, TEO_PORT)
        d = protocol.request(request)
        d.addCallback(self.processResponse)

    def processResponse(self, response):
        if response.code == coap.CREATED:
            if response.opt.location_path:
                self.reg_upd_address = response.opt.location_path
                reactor.callLater(10, self.updateRegistration)
        elif response.code == coap.CHANGED:
            reactor.callLater(10, self.updateRegistration)
        else:
            reactor.callLater(30, self.resolveAddress)

    def updateRegistration(self):
        """
        Method for updating registration on the server. We assume that resources
        do not change, therefore no payload is sent and constant Etag is used.
        """
        request = coap.Message(code=coap.PUT, payload="")
        request.opt.uri_path = self.reg_upd_address
        request.opt.etag = CONST_ETAG
        request.remote = (self.teo_ip_address, TEO_PORT)
        d = protocol.request(request)
        d.addCallback(self.processResponse)

log.startLogging(sys.stdout)

temperature_sensor = TemperatureSensor()

root = resource.CoAPResource()

node = resource.CoAPResource()
root.putChild('node', node)

status = StatusResource()
node.putChild('status', status)

temp = TemperatureResource(temperature_sensor)
node.putChild('temp', temp)

endpoint = resource.Endpoint(root)
protocol = coap.Coap(endpoint)
client = Agent(protocol)

reactor.listenUDP(coap.COAP_PORT, protocol)
reactor.run()
