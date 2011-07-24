# Copyright (C) 2011 by jedi95 <jedi95@gmail.com> and 
#                       CFSworks <CFSworks@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import socket
import httplib
import Miner
import traceback

from base64 import b64encode
from json import dumps, loads
from twisted.internet import defer, reactor, threads
from collections import deque
from time import sleep
from urlparse import urlsplit
from threading import Thread

from ClientBase import ClientBase, AssignedWork

#constants
USER_AGENT = 'phoenix/' + Miner.Miner.VERSION

MAX_REDIRECTS = 3

LONG_POLL_TIMEOUT = 3600

TIMEOUT = 10


# Socket wrapper to enable socket.TCP_NODELAY and KEEPALIVE
realsocket = socket.socket
def socketwrap(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
    sockobj = realsocket(family, type, proto)
    sockobj.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sockobj.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    return sockobj
socket.socket = socketwrap

#exceptions
class ServerMessage(Exception): pass
class NotAuthorized(Exception): pass

#This RPCClient is based on the RPC implementation in poclbm, but it has been
#modified to work within the Phoenix framework and make use of deferreds
class RPCClient(ClientBase):
    #The actual root of the whole RPC client system.
    
    def __init__(self, handler, url):
        self.handler = handler
        self.params = {}
        for param in url.params.split('&'):
            s = param.split('=',1)
            if len(s) == 2:
                self.params[s[0]] = s[1]
        try:
           self.askrate = int(self.params['askrate'])
        except (KeyError, ValueError):
           self.askrate = None
        self.connected = False
        self.version = 'RPCClient/1.6'
        self.host = str(url.hostname) + ':' + str(url.port)
        self.postdata = {"method": 'getwork', 'id': 'json'}
        self.headers = {"User-Agent": USER_AGENT, "Authorization": 'Basic ' + b64encode('%s:%s' % (url.username, url.password)), "Content-Type": 'application/json'}
        self.getConnection = None
        self.sendConnection = None
        self.longPollURL = None
        self.deferredQueue = deque()
        self.getWaiting = False
        self.sending = False
        self.getting = False
        self.block = ''
        self.disconnected = False
    
    #thread safe log callback
    def log(self, message):
        reactor.callFromThread(self.runCallback, 'log', message)
        
    #thread safe debug callback
    def debug(self, message):
        reactor.callFromThread(self.runCallback, 'debug', message)
        
    #thread safe MSG callback
    def msg(self, message):
        reactor.callFromThread(self.runCallback, 'debug', message)
        
    #connects to the server by requesting work
    def connect(self):
        self.requestWork()
        LPThread = Thread(target=self.longPollThread)
        LPThread.daemon = True
        LPThread.start()
    
    #disconnect and do not attempt to reconnect
    def disconnect(self):
        self.disconnected = True
        self.deferredQueue.clear()
        self.LPThread = None
    
    #this needs to be present as part of the Phoenix framework
    def setMeta(self, var, value):
        #RPC clients do not support meta. Ignore.
        pass
    
    #handles sending connect/disconnect messages to the logger
    def setConnected(self, connected):
        if self.connected:
            if connected != self.connected:
                self.runCallback('disconnect')
                self.longPollURL = None
        else:
            if connected != self.connected:
                self.runCallback('connect')                
                
        self.connected = connected
    
    def setVersion(self, shortname, longname=None, version=None, author=None):
        if version is not None:
            self.version = '%s/%s' % (shortname, version)
        else:
            self.version = shortname
    
    #This is called to asyncronously request more work from the protocol
    #When work is recieved, it will be added to WorkQueue
    def requestWork(self):
    
        if self.getting:
            #set a flag to ensure more work is requested after the current
            #request finishes
            self.getWaiting = True
        else:
            #set getting flag to prevent multiple getworks at a time
            self.getting = True
            
            #set up the connection if it's not already
            if not self.getConnection:
                self.getConnection = httplib.HTTPConnection(self.host, strict=True, timeout=TIMEOUT)
                
            def callback(x):
                
                #process recieved work and add to WorkQueue
                self.handleWork(x)
                
                #if more work was requested then start another getwork
                if self.getWaiting:
                    self.getting = False
                    self.getWaiting = False
                    self.requestWork()
                else:
                    #otherwise set getting to false
                    self.getting = False
                    
                    #handle askrate if set
                    if self.askrate is not None:
                        reactor.callLater(self.askrate, self.requestWork)
                        
            def errback(x):
                
                #report error as debug
                self.runCallback('debug', 'Error getting work: ' + str(x))
                
                #since we are not imediately requesting more work, set getting
                #to false in case another request is sent
                self.getting = False
                
                #since we failed to get work try again in 15 seconds
                reactor.callLater(15, self.requestWork)
            
            d = threads.deferToThread(self.getwork, self.getConnection)
            d.addErrback(errback)
            d.addCallback(callback)
            return d
    
    #handles work returned by getwork()
    def handleWork(self, work, pushed=False):
        if work is None:
            return;
        
        aw = AssignedWork()
        aw.data = work['data'].decode('hex')[:80]
        aw.target = work['target'].decode('hex')
        aw.mask = work.get('mask', 32)
        if pushed:
            self.runCallback('push', aw)
        self.runCallback('work', aw)
    
    #getwork (warning: not called from main thread!)
    def getwork(self, connection, data=None):
        if self.disconnected: return
        
        try:
            self.postdata['params'] = [data] if data else []
            (connection, result) = self.request(connection, '/', self.headers, dumps(self.postdata))
            
            reactor.callFromThread(self.setConnected, True)
            return result['result']
        
        except NotAuthorized:
            reactor.callFromThread(self.runCallback, 'failure')
            self.log('Wrong username or password')
            self.connectionLost()
        except ServerMessage as message:
            self.msg(message)
            self.connectionLost()
        except (IOError, httplib.HTTPException, ValueError) as e:
            if self.connected:
                self.setConnected(False)
            else:
                reactor.callFromThread(self.runCallback, 'failure')
                
        except:
            self.debug('Unknown error')
            self.connectionLost()
    
    #request (warning: not called from main thread!)
    def request(self, connection, url, headers, data=None):
        result = response = None
        try:
            if data: connection.request('POST', url, data, headers)
            else: connection.request('GET', url, headers=headers)
            
            response = connection.getresponse()
            if response.status == httplib.UNAUTHORIZED: raise NotAuthorized()
            r = MAX_REDIRECTS
            while response.status == httplib.TEMPORARY_REDIRECT:
                response.read()
                url = response.getheader('Location', '')
                if r == 0 or url == '': raise HTTPException('Too many redirects or bad redirects')
                connection.request('GET', url, headers=headers)
                response = connection.getresponse();
                r -= 1
            
            #Check for server messages
            result = loads(response.read())
            if result['error']: raise ServerMessage(result['error']['message'])
            
            #handle block number
            blocknum = response.getheader('X-Blocknum', None)
            if blocknum is not None:
                reactor.callFromThread(self.handleBlockNum, blocknum)
            
            #Handle long polling
            longPollURL = response.getheader('X-Long-Polling', None)
            if longPollURL is not None:
                self.longPollURL = longPollURL
            
            return (connection, result)
        finally:
            if not result or not response or (response.version == 10 and response.getheader('connection', '') != 'keep-alive') or response.getheader('connection', '') == 'close':
                connection.close()
                connection = None
    
    #handles X-Blocknum header and block callback
    def handleBlockNum(self, blocknum):
        try:
            block = int(blocknum)
        except (TypeError, ValueError): pass
        else:
            if self.block != block:
                self.block = block
                self.runCallback('block', block)
    
    def sendResult(self, result):
        #Sends a result to the server, returning a Deferred that fires with
        #a bool to indicate whether or not the work was accepted.
        
        # Must be a 128-byte response, but the last 48 are typically ignored.
        result += '\x00'*48
        
        #This adds work to the pending pool, if no other work is being sent
        #it will go through imediately, otherwise it will wait
        d = self.sendWork(result.encode('hex'))
        
        def errback(message):
            self.runCallback('debug', str(message))
            return False # ANY error while turning in work is a Bad Thing(TM).
            
        #we need to return the result, not the headers
        def callback(x):
            try:
                accepted = x
            except TypeError:
                return False
            return accepted
        
        d.addErrback(errback)
        d.addCallback(callback)
        return d
    
    #used internally only, do not call from other classes
    def sendWork(self, data):
        
        if self.sending:
            df = defer.Deferred()
            self.deferredQueue.append((df, data))
            return df
        else:
            
            def callback(accepted):
                if len(self.deferredQueue) > 0:
                    self.continueSend()
                else:
                    self.sending = False
                    
                return defer.succeed(accepted)
            
            def errback(x):
            
                self.runCallback('debug', 'Error sending work: ' + str(x))
                if len(self.deferredQueue) > 0:
                    self.continueSend()
                else:
                    self.sending = False
                    
                return defer.succeed(False)
            
            if not self.sendConnection:
                self.sendConnection = httplib.HTTPConnection(self.host, strict=True, timeout=TIMEOUT)
            
            self.sending = True
            d = threads.deferToThread(self.getwork, self.sendConnection, data)
            d.addCallback(callback)
            d.addErrback(errback)
            
            return d
    
    #continues to send work until deferred queue is empty
    def continueSend(self):
        
        def callback(accepted):
            if len(self.deferredQueue) > 0:
                self.continueSend()
            else:
                self.sending = False
            
            return accepted
            
        def errback(x):
            self.runCallback('debug', 'Error sending work: ' + str(x))
            if len(self.deferredQueue) > 0:
                self.continueSend()
            else:
                self.sending = False
                
            return False
        
        df, data = self.deferredQueue.popleft()
        if not self.sendConnection:
            self.sendConnection = httplib.HTTPConnection(self.host, strict=True, timeout=TIMEOUT)
        
        self.sending = True
        d = threads.deferToThread(self.getwork, self.sendConnection, data)
        d.addCallback(callback)
        d.addErrback(errback)
        d.chainDeferred(df)
    
    #long polling thread
    def longPollThread(self):
        connection = None
        last_url = None
        while not self.disconnected:
            sleep(1)
            url = self.longPollURL
            if url is None:
                #inform miner that long polling is not active
                reactor.callFromThread(self.runCallback, 'longpoll', False)
            else:
                reactor.callFromThread(self.runCallback, 'longpoll', True)
                host = self.host
                parsedUrl = urlsplit(url)
                if parsedUrl.netloc != '':
                    host = parsedUrl.netloc
                    url = url[url.find(host)+len(host):]
                    if url == '': url = '/'
                try:
                    if self.longPollURL != last_url:
                        self.debug("Using new LP URL " + str(url))
                        connection = None
                    if not connection:
                        self.debug("LP connected to " + str(host))
                        connection = httplib.HTTPConnection(host, timeout=LONG_POLL_TIMEOUT)
                        
                    (connection, result) = self.request(connection, url, self.headers)
                    reactor.callFromThread(self.handleWork, result['result'])
                    reactor.callFromThread(self.runCallback, 'push', True)
                    last_url = self.longPollURL
                except NotAuthorized:
                    self.log('Long poll: Wrong username or password')
                except ServerMessage as e:
                    self.log('Long poll: ' + str(e))
                except (IOError, httplib.HTTPException, ValueError):
                    self.log('Long poll exception:')
                    traceback.print_exc()
        