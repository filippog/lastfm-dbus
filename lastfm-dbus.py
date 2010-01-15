#!/usr/bin/python
# Copyright © 2009 - Filippo Giunchedi <filippo@esaurito.net>
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.

import httplib
import time
import urllib
from md5 import md5

import dbus
import dbus.service
import gobject
from dbus.mainloop.glib import DBusGMainLoop

MAIN_INTERFACE="net.esaurito.LastFM"

CLIENT_ID = 'tst'
CLIENT_VERSION = '1.0'
PROTOCOL_VERSION = '1.2'

CACHE_DIR = os.environ['HOME']
CACHE_FILE = ".lastfm-dbus.db"

class LastFM(dbus.service.Object):
    """
    Main lastfm-dbus class, this ought to implement the audioscrobbler protocol
    (version 1.2).

    See http://www.audioscrobbler.net/development/protocol/
    """

    def __init__(self, bus, busname):
        dbus.service.Object.__init__(self, bus, busname)
        self.offline = False

        self.sess_id = None
        self.np_url = None
        self.submit_url = None

    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='sss', out_signature='s')

# XXX provide LoginRaw to not pass cleartext password over the bus
    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='ss', out_signature='s')
    def Login(self, user, password_hash):
# XXX signal authentication status on completion i.e. make this asynchronous
        """Login into audioscrobbler with given user and md5-hashed password."""

        tstamp = int(time.time())

        self.user = user
        self.auth_token = md5(password_hash + str(tstamp)).hexdigest()
#        self.auth_token = md5(md5(password).hexdigest() + str(tstamp)).hexdigest()

        request = {'hs': 'true', 'p': str(PROTOCOL_VERSION),
                    'c': str(CLIENT_ID), 'v': str(CLIENT_VERSION),
                    'u': self.user,
                    't': tstamp,
                    'a': self.auth_token}

        #response = _scrobbler_get("a=%s&c=tst&hs=true&p=1.2&u=%s&t=%s&v=1.0" % (self.auth_token, self.user, tstamp))
        response = self._scrobbler_request("GET",
                "http://post.audioscrobbler.com/?" + urllib.urlencode(request))

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
        """Send current playing artist and track, this data will not be
        scrobbled. Provide required data only."""

        return self.NowPlayingFull(artist, track, '', '', '', '')

# XXX emit signal on successful submit
    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='sssiis', out_signature='s')
    def NowPlayingFull(self, artist, track, album, length, trackno, mbid):
        """Send current playing song and additional info, this data will not be
        scrobbled."""

        if not self.sess_id or not self.np_url:
            return "NOSESSION"

        if self.offline:
            return 'OFFLINE'

        request = {'s': self.sess_id,
                   'a': artist, 't': track,
                   'b': album, 'l': length,
                   'n': trackno, 'm': mbid}

        response = self._scrobbler_request("POST", self.np_url,
                "&" + urllib.urlencode(request))

        if not response:
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
        """Submit data to audioscrobbler, this is the minimum data required by
        protocol.
        """

        return self.SubmitFull(artist, track, starttime, 'P', '', '', '', '', '')

# XXX emit signal on successful submit?
    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='ssissisii', out_signature='s')
    def SubmitFull(self, artist, track, starttime, source, rating, length,
            album, trackno, mbid):
        """Submit data to audioscrobbler.

        While in offline status data will be queued for later submission.
        """

        if not self.sess_id or not self.submit_url:
            return "NOSESSION"

        if self.offline:
            self._enqueue(request)
            return 'QUEUED'

        request = {'s': self.sess_id,
                   'a[0]': artist, 't[0]': track,
                   'i[0]': starttime, 'o[0]': source,
                   'r[0]': rating, 'l[0]': length,
                   'b[0]': album, 'n[0]': trackno,
                   'm[0]': mbid}

        response = self._scrobbler_request("POST", self.submit_url,
                "&" + urllib.urlencode(request))

        if not response:
            self._enqueue(request)
            return 'QUEUED'

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

        The status is defined as the ability to send submissions to
        audioscrobbler.
        In OFFLINE mode submissions will be cached and sent after the first
        successful authentication.
        """
        if self.offline:
            return "OFFLINE"
        else:
            return "ONLINE"

    @dbus.service.method(dbus_interface=MAIN_INTERFACE,
                         in_signature='s', out_signature='')
    def SetStatus(self, status):
        """Set lastfm-dbus status either ONLINE or OFFLINE."""

        if status == 'OFFLINE':
            if not self.offline:
                self.StatusChanged('OFFLINE')
            self.offline = True
        else:
# XXX flush queue?
#            self._flush_queue()
            if self.offline:
                self.StatusChanged('ONLINE')
            self.offline = False

# Signals
    @dbus.service.signal(dbus_interface=MAIN_INTERFACE,
                         signature='s')
    def StatusChanged(self, status):
        """This signal is sent whenever the status is changed either manually
        or after a successful submission."""

        return status

    def _scrobbler_request(self, method, request, body=''):
        """Send via HTTP using given method and request, set status on
        connection failure."""
# XXX handle exceptions
# XXX reuse connections? hard unless HTTPResponse has been completely read()
        address = request.split('/')[2]
        conn = httplib.HTTPConnection(address)
        #conn.set_debuglevel(100)

        try:
            conn.connect()
        except socket.error:
            self.SetStatus('OFFLINE')
            return None

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
# retrieve stored requests, pack up ITEM_PER_REQUEST requests as a single request
    def _flush_queue(self):
        ret = 0
        if not self.sess_id:
            return ret

        cache = os.path.join(CACHE_DIR, CACHE_FILE)
        if not os.path.exists(cache):
            return None
        else:
            c = cPickle.load(open(cache))

        ITEM_PER_REQUEST = 10

        # list of requests
        for i in range(0, len(c), ITEM_PER_REQUEST):
            # add session indentifier
            # update request dictionary with appropriate keys
            # submit
            request = {}
            request['s'] = self.sess_id

            for j,e in enumerate(c[i:i+ITEM_PER_REQUEST]):
                request.update(
                            [ ("%s[%d]" % (k, j), v) for k,v in e.items() ]
                        )

# XXX check for error
            response = self._scrobbler_request("POST", self.submit_url,
                    "&" + urllib.urlencode(request))
            if response and response.status == 200:
                del c[i:i+ITEM_PER_REQUEST]
                ret += 1

        cPickle.dump(c, open(cache, 'w'), 2)

        return ret

# store given request to disk
    def _enqueue(self, request):
        cache = os.path.join(CACHE_DIR, CACHE_FILE)
        if os.path.exists(cache):
            c = cPickle.load(open(cache))
        else:
            c = []

        # strip [0] indices from keys
        tostore = dict([(k[0], v) for k,v in request.items()])
        # remove session, useless
        del tostore['s']

        c.append(tostore)
        cPickle.dump(c, open(cache, 'w'), 2)

        return True
#        f = open(os.path.join(BASEDIR, REQUEST_STORE), "w")
#        pass


def main():
    DBusGMainLoop(set_as_default=True)

    session_bus = dbus.SessionBus()
    name = dbus.service.BusName(MAIN_INTERFACE, session_bus)
    obj = LastFM(session_bus, MAIN_INTERFACE.replace('.', '/'))

    loop = gobject.MainLoop()
    loop.run()


if __name__ == '__main__':
    main()

# vim:et:ts=4
