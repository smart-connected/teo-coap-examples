import sys
import socket
import msgpack

from twisted.internet import defer, reactor, threads
from twisted.python import log
from twisted.application import service, internet

import txthings.coap as coap
import txthings.resource as resource

from w1thermsensor import W1ThermSensor
import pigpio


STATUS_PS = (1 << 0)
MSGPACK_MEDIA_TYPE = 59 #number is unofficial
CONST_ETAG = "a"
REGISTRATION_UPDATE_PERIOD = 3600

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

class LEDActuator():
    """
    Use this class to control RaspberryPi's GPIO - for example LED.
    We use 3rd party module 'pigpio' which might not be
    asynchronous - that's why we use deferToThread function.
    """
    def __init__(self):
        self.gpio = pigpio.pi()
        self.gpio.set_mode(17, pigpio.OUTPUT)
	self.gpio.write(17,1)
	self.state = 0

    def turnOn(self):
        self.state = 1
        return threads.deferToThread(self.gpio.write,17, 0)

    def turnOff(self):
        self.state = 0
        return threads.deferToThread(self.gpio.write,17, 1)

    def getState(self):
        return self.state

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

    def __init__(self):
        resource.CoAPResource.__init__(self)
        self.visible = True
        self.addParam(resource.LinkParam("title", "Temperature resource"))
        self.sensor = TemperatureSensor()

    def render_GET(self, request):
        value = self.sensor.temperature
        if value is not None:
            response = coap.Message(code=coap.CONTENT, payload=msgpack.packb(value))
            response.opt.content_format = MSGPACK_MEDIA_TYPE
        else:
	    response = coap.Message(code=coap.SERVICE_UNAVAILABLE)
        return defer.succeed(response)

class LEDResource (resource.CoAPResource):
    """Resource that represents 1-wire temperature sensor readout"""

    def __init__(self):
        resource.CoAPResource.__init__(self)
        self.visible = True
        self.addParam(resource.LinkParam("title", "LED resource"))
        self.led = LEDActuator()

    def render_GET(self, request):
        response = coap.Message(code=coap.CONTENT, payload=msgpack.packb(self.led.getState()))
        return defer.succeed(response)

    def render_PUT(self, request):
        value = msgpack.unpackb(request.payload)
        if value not in (0, 1):
	    response = coap.Message(code=coap.BAD_REQUEST)
            return defer.succeed(response)
	if value == 1:
            d = self.led.turnOn()
	elif value == 0:
            d = self.led.turnOff()
        d.addCallback(self.responseReady)
        return d

    def responseReady(self, value):
        response = coap.Message(code=coap.CHANGED)
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
        #data = []
        #self.protocol.endpoint.resource.generateResourceList(data, "")
        #payload = ",".join(data)
        payload = '' #
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
                reactor.callLater(REGISTRATION_UPDATE_PERIOD, self.updateRegistration)
        elif response.code == coap.CHANGED:
            reactor.callLater(REGISTRATION_UPDATE_PERIOD, self.updateRegistration)
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

def initialize_endpoint():
    root = resource.CoAPResource()

    node = resource.CoAPResource()
    root.putChild('node', node)

    status = StatusResource()
    node.putChild('status', status)

    temp = TemperatureResource()
    node.putChild('temp', temp)

    led = LEDResource()
    node.putChild('led', led)

    return resource.Endpoint(root)



if __name__ == '__main__':
    log.startLogging(sys.stdout)
    protocol = coap.Coap(initialize_endpoint())
    client = Agent(protocol)
    reactor.listenUDP(coap.COAP_PORT, protocol)
    reactor.run()
else:
    protocol = coap.Coap(initialize_endpoint())
    client = Agent(protocol)
    application = service.Application("teo_client")
    internet.UDPServer(coap.COAP_PORT, protocol).setServiceParent(application)



