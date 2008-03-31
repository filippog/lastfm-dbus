#!/usr/bin/python
import httplib
import time
import urllib
from md5 import md5

import dbus
import dbus.service
import gobject
from dbus.mainloop.glib import DBusGMainLoop

MAIN_INTERFACE="net.esaurito.LastFM"

class LastFM(dbus.service.Object):
    """
    Main lastfm-dbus class, this ought to implement the audioscrobbler protocol.
    See http://www.audioscrobbler.net/development/protocol/
    """
    def __init__(self, bus, busname):
        dbus.service.Object.__init__(self, bus, busname)
        self.offline = False

        self.sess_id = None
        self.np_url = None
        self.submit_url = None

# XXX provide LoginRaw to not pass cleartext password over the bus
    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='ss', out_signature='s')
    def Login(self, user, password):
# XXX signal authentication status on completion i.e. make this asynchronous
        """Login into audioscrobbler with given user and password"""

        tstamp = int(time.time())

        self.user = user
        self.auth_token = md5(md5(password).hexdigest() + str(tstamp)).hexdigest()
        
        request = {'hs': 'true', 'p': '1.2',
                    'c': 'tst', 'v': '1.0',
                    'u': self.user,
                    't': tstamp,
                    'a': self.auth_token}
    
        #response = _scrobbler_get("a=%s&c=tst&hs=true&p=1.2&u=%s&t=%s&v=1.0" % (self.auth_token, self.user, tstamp))
        response = self._scrobbler_request("GET", "http://post.audioscrobbler.com/?" + urllib.urlencode(request))

        if self.offline:
            return "OFFLINE"

        #print response.msg
        if response.status != 200:
            return 'HARDFAIL ' + str(response.status)

        r = response.read().split('\n')
        
# XXX possibile?
        if len(r) == 0:
            return 'HARDFAIL no response'

# XXX retry on hard failures as specified by protocol
        if r[0] == 'OK':
            self.sess_id = r[1]
            self.np_url = r[2]
            self.submit_url = r[3]
            self._flush_queue()
            return 'OK'
        elif r[0] in ('BANNED', 'BADAUTH', 'BADTIME'):
            return r[0]
        elif r[0].startswith('FAILED'):
            return r[0]

        return 'HARDFAIL'

    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='ss', out_signature='s')
    def NowPlaying(self, artist, track):
        """Send current playing artist and track, this data will not be scrobbled"""
        return self.NowPlayingFull(artist, track, '', '', '', '')

    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='sssiis', out_signature='s')
    def NowPlayingFull(self, artist, track, album, length, trackno, mbid):
        """Send current playing song and additional info, this data will not be scrobbled"""
        if not self.sess_id or not self.np_url:
            return "NOSESSION"

        request = {'s': self.sess_id,
                   'a': artist, 't': track,
                   'b': album, 'l': length,
                   'n': trackno, 'm': mbid}

        response = self._scrobbler_request("POST", self.np_url, "&" + urllib.urlencode(request))

        if self.offline:
            return 'OFFLINE'

        if response.status != 200:
            return 'HARDFAIL ' + str(response.status)

        r = response.read().split('\n')
        if r[0] in ('OK', 'BADSESSION'):
            return r[0] 
        elif r[0].startswith("FAILED"):
            return r[0]

        return 'HARDFAIL'


    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='ssi', out_signature='s')
    def Submit(self, artist, track, starttime):
        """Submit data to audioscrobbler, this is the minimum required by protocol"""
        return self.SubmitFull(artist, track, starttime, 'P', '', '', '', '', '')

    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='ssissisii', out_signature='s')
    def SubmitFull(self, artist, track, starttime, source, rating, length, album, trackno, mbid):
        """Submit data to audioscrobbler"""
        if not self.sess_id or not self.submit_url:
            return "NOSESSION"

        request = {'s': self.sess_id,
                   'a[0]': artist, 't[0]': track,
                   'i[0]': starttime, 'o[0]': source,
                   'r[0]': rating, 'l[0]': length,
                   'b[0]': album, 'n[0]': trackno, 
                   'm[0]': mbid}

        response = self._scrobbler_request("POST", self.submit_url, "&" + urllib.urlencode(request))

        if self.offline:
            self._enqueue(request)
            return 'OFFLINE'

        if response.status != 200:
            return 'HARDFAIL ' + str(response.status)

        r = response.read().split('\n')
        if r[0] in ('OK', 'BADSESSION'):
            return r[0] 
        elif r[0].startswith("FAILED"):
            return r[0]

        return 'HARDFAIL'

    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='', out_signature='s')
    def GetStatus(self):
        """Get lastfm-dbus status either ONLINE or OFFLINE.
           
           The status is defined as the ability to send submissions to audioscrobbler.
           In OFFLINE mode submissions will be cached and sent after the first successful authentication.
        """
        if self.offline:
            return "OFFLINE"
        else:
            return "ONLINE"
    
    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='s', out_signature='')
    def SetStatus(self, status):
        """Set lastfm-dbus status either ONLINE or OFFLINE"""
        if status == 'OFFLINE':
            if not self.offline:
                self.StatusChanged('OFFLINE') 
            self.offline = True
        else:
            if self.offline:
                self.StatusChanged('ONLINE') 
            self.offline = False

# Signals
    @dbus.service.signal(dbus_interface=MAIN_INTERFACE,
                         signature='s')
    def StatusChanged(self, status):
        """This signal is sent whenever the status is changed either manually or after a successful submission"""
        return status

    def _scrobbler_request(self, method, request, body=''):
        """Send via HTTP using given method and request, set status on connection failure"""
# XXX handle exceptions
# XXX reuse connections?
        address = request.split('/')[2]
        conn = httplib.HTTPConnection(address)
        #conn.set_debuglevel(100)

# XXX handle being offline
        try:
            conn.connect()
        except socket.error:
            self.SetStatus('OFFLINE') 
            return -1 # XXX fixme

        #if method == 'POST':
            #request = "/" + "".join(request.split('/')[3:])

        conn.putrequest(method, request)
        conn.putheader('Accept-Charset', 'utf-8')
        conn.putheader('Content-Type', 'application/x-www-form-urlencoded')
        if body:
            conn.putheader('Content-Length', len(body))
        conn.endheaders()

        if body:
            conn.send(body)

        return conn.getresponse()

# XXX implement
    def _flush_queue(self):
        pass
    
    def _enqueue(self, request):
        pass


def main():
    DBusGMainLoop(set_as_default=True)

    session_bus = dbus.SessionBus()
    name = dbus.service.BusName("net.esaurito.LastFM", session_bus)
    object = LastFM(session_bus, '/net/esaurito/LastFM')

    loop = gobject.MainLoop()
    loop.run()
    

if __name__ == '__main__':
    main()

# vim:et:ts=4
