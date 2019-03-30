from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE
from io import BlockingIOError
from collections import namedtuple


class CoroutineWrapper:
    """wrap a generator so that it can be awaited like a coroutine"""
    def __init__(self, generator):
        self.generator = generator

    def __await__(self):
        return (yield from self.generator)


def coroutine(generator):
    """decorator for generator functions, to make it awaitable"""
    def wrapper(*args, **kwargs):
        co = generator(*args, **kwargs)
        return CoroutineWrapper(co)
    return wrapper


class StreamSocket:
    """async TCP socket"""
    def __init__(self, loop, sock):
        self.sock = sock

        sock.setblocking(0)

    @coroutine
    def accept(self):
        return (yield EventLoopCommand.StreamAccept(self.sock))

    @coroutine
    def connect(self, addr):
        return (yield EventLoopCommand.StreamConnect(self.sock, addr))

    @coroutine
    def recv(self, buf_len, flags=0):
        return (yield EventLoopCommand.StreamRecv(self.sock, buf_len, flags))

    @coroutine
    def send(self, buf, flags=0):
        return (yield EventLoopCommand.StreamSend(self.sock, buf, flags))

    async def sendall(self, buf, flags=0):
        target_len = len(buf)
        bytes_sent = 0
        while bytes_sent < target_len:
            await self.send(buf[bytes_sent:], flags)


class EventLoopCommand:
    StreamAccept = namedtuple('StreamAccept', ['sock'])
    StreamConnect = namedtuple('StreamConnect', ['sock', 'addr'])
    StreamRecv = namedtuple('StreamRecv', ['sock', 'buf_len', 'flags'])
    StreamSend = namedtuple('StreamSend', ['sock', 'buf', 'flags'])


class EventLoop:
    """event loop, handles IO"""
    def __init__(self):
        self.co_result = {}

        self.selector = DefaultSelector()

    def run_until_complete(self, coroutine):
        self._continue(coroutine, None)
        while coroutine not in self.co_result:
            self._tick()
        result = self.co_result[coroutine]
        del self.co_result[coroutine]
        return result

    def run_forever(self):
        while True:
            self._tick()

    def start_coroutine(self, co):
        self._continue(co, None)

    def _continue(self, co, awaited):
        try:
            cmd = co.send(awaited)
        except StopIteration as e:
            self.co_result[co] = e.value
        else:
            if isinstance(cmd, EventLoopCommand.StreamAccept):
                self._register(EVENT_READ, cmd.sock, (cmd, co))
            elif isinstance(cmd, EventLoopCommand.StreamConnect):
                try:
                    cmd.sock.connect(cmd.addr)
                except BlockingIOError:
                    self._register(EVENT_WRITE, cmd.sock, (cmd, co))
                except:
                    raise
                else:
                    # connect finished immediately
                    self._continue(co, None)
            elif isinstance(cmd, EventLoopCommand.StreamRecv):
                self._register(EVENT_READ, cmd.sock, (cmd, co))
            elif isinstance(cmd, EventLoopCommand.StreamSend):
                self._register(EVENT_WRITE, cmd.sock, (cmd, co))
            else:
                raise RuntimeError('unknown cmd')

    def _register(self, event, sock, data):
        try:
            key = self.selector.get_key(sock)
        except KeyError:
            self.selector.register(sock, event, {event: data})
        else:
            assert key.events & event == 0
            key.data[event] = data
            self.selector.modify(sock, key.events | event, key.data)

    def _unregister(self, event, sock):
        key = self.selector.get_key(sock)
        assert key.events & event != 0
        new_mask = key.events & ~event
        del key.data[event]
        if new_mask == 0:
            self.selector.unregister(sock)
        else:
            self.selector.modify(sock, new_mask, key.data)

    def _tick(self):
        for key, mask in self.selector.select(1.0):
            if mask & EVENT_WRITE != 0:
                cmd, co = key.data[EVENT_WRITE]
                self._unregister(EVENT_WRITE, cmd.sock)
                if isinstance(cmd, EventLoopCommand.StreamConnect):
                    self._continue(co, cmd.sock.connect(cmd.addr))
                elif isinstance(cmd, EventLoopCommand.StreamSend):
                    self._continue(co, cmd.sock.send(cmd.buf))
                else:
                    raise RuntimeError('unknown cmd')
            elif mask & EVENT_READ != 0:
                cmd, co = key.data[EVENT_READ]
                self._unregister(EVENT_READ, cmd.sock)
                if isinstance(cmd, EventLoopCommand.StreamAccept):
                    self._continue(co, cmd.sock.accept())
                elif isinstance(cmd, EventLoopCommand.StreamRecv):
                    self._continue(co, cmd.sock.recv(cmd.buf_len))
                else:
                    raise RuntimeError('unknown cmd')


if __name__ == '__main__':
    # start a echo server

    async def main(loop):
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('0.0.0.0', 11451))
        sock.listen(2)
        stream = StreamSocket(loop, sock)
        while True:
            conn, addr = await stream.accept()
            conn = StreamSocket(loop, conn)
            loop.start_coroutine(echo_server(conn))

    async def echo_server(stream):
        while True:
            data = await stream.recv(4096)
            if len(data) == 0:
                break
            await stream.send(data)

    loop = EventLoop()
    loop.start_coroutine(main(loop))
    loop.run_forever()



