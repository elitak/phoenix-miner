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

import time
from Queue import Queue
from twisted.internet import reactor, defer

class QueueReader(object):
    """A QueueReader is a very efficient WorkQueue reader that keeps the next
    nonce range available at all times. The benefit is that threaded mining
    kernels waste no time getting the next range, since this class will have it
    completely requested and preprocessed for the next iteration.
    
    The QueueReader is iterable, so a dedicated mining thread needs only to do
    for ... in self.qr:
    """
    
    def __init__(self, interface, preprocessor=None, workSizeCallback=None):
        self.interface = interface
        self.preprocessor = preprocessor
        self.workSizeCallback = workSizeCallback
        
        if self.preprocessor is not None:
            if not callable(self.preprocessor):
                raise TypeError('the given preprocessor must be callable')
        if self.workSizeCallback is not None:
            if not callable(self.workSizeCallback):
                raise TypeError('the given workSizeCallback must be callable')
        
        # This shuttles work to the dedicated thread.
        self.dataQueue = Queue()
        
        # Used in averageing the second-to-last and third-to-last execution
        # times.
        self.executionTimeSamples = []
        self.averageExecutionTime = None
        
        # This gets changed by _updateWorkSize.
        self.executionSize = None
        
        # Statistics accessed by the dedicated thread.
        self.currentData = None
        self.startedAt = None
        
        reactor.addSystemEventTrigger('during', 'shutdown', self._shutdown)
        
    def start(self):
        """Called by the kernel when it's actually starting."""
        self._requestMore()
    
    def _ranExecution(self, dt, size):
        """An internal function called after an execution completes, with the
        time it took. Used to keep track of the time so kernels can use it to
        tune their execution times.
        """
        
        if dt > 0:
            self.interface.updateRate(int(size/dt/1000))
        
        self.executionTimeSamples.append(dt)
        self.executionTimeSamples = self.executionTimeSamples[-3:]
        
        if len(self.executionTimeSamples) == 3:
            self.averageExecutionTime = (self.executionTimeSamples[0] +
                self.executionTimeSamples[1]) / 2

            self._updateWorkSize(size)
    
    def _updateWorkSize(self, size):
        """An internal function that tunes the executionSize to that specified
        by the workSizeCallback; which is in turn passed the average of the
        second-to-last and third-to-last execution times.
        """
        if self.workSizeCallback and self.averageExecutionTime is not None:
            self.executionSize = self.workSizeCallback(
                self.averageExecutionTime, size)
    
    def _requestMore(self):
        """This is used to start the process of making a new item available in
        the dataQueue, so the dedicated thread doesn't have to block.
        """
        
        # This should only run if there's no ready-to-go work in the queue.
        if not self.dataQueue.empty():
            return
        
        if self.executionSize is None:
            d = self.interface.fetchRange()
        else:
            d = self.interface.fetchRange(self.executionSize)
        
        if self.preprocessor:
            d.addCallback(self.preprocessor)
        d.addCallback(self.dataQueue.put_nowait)
    
    def _shutdown(self):
        """Called when the reactor quits."""
        # Tell the other thread to exit cleanly.
        while not self.dataQueue.empty():
            self.dataQueue.get(False)
        self.dataQueue.put(StopIteration())
    
    def __iter__(self):
        return self
    def next(self):
        """Since QueueReader is iterable, this is the function that runs the
        for-loop and dispatches work to the thread.
        
        This should be the only thread that executes outside of the Twisted
        main thread.
        """
        
        # If we just completed a range, we should tell the main thread.
        now = time.time()
        if self.currentData:
            dt = now - self.startedAt
            reactor.callFromThread(self._ranExecution, dt,
                self.currentData.getHashCount())
        self.startedAt = now
        
        # Block for more data from the main thread. In 99% of cases, though,
        # there should already be something here.
        self.currentData = self.dataQueue.get(True)
        
        # Does the main thread want us to shut down, or pass some more data?
        if isinstance(self.currentData, StopIteration):
            raise self.currentData
        
        # We just took the only item in the queue. It needs to be restocked.
        reactor.callFromThread(self._requestMore)
        
        return self.currentData