"""Bounded RESP2 client for the original host-to-logical-database protocol."""
import socket


class ProtocolError(ValueError):
    pass


class Redis:
    def __init__(self, host="127.0.0.1", port=6379, password=None, username="ocrun-admin"):
        self.host, self.port, self.password = host, port, password
        self.username = username

    @staticmethod
    def encode(args):
        values = [str(a).encode("utf-8") for a in args]
        return b"*%d\r\n" % len(values) + b"".join(b"$%d\r\n" % len(v) + v + b"\r\n" for v in values)

    @classmethod
    def decode(cls, stream, depth=0):
        if depth > 16:
            raise ProtocolError("RESP nesting limit")
        line = stream.readline(1048577)
        if len(line) > 1048576 or not line.endswith(b"\r\n"):
            raise ProtocolError("Incomplete RESP response")
        kind, value = line[:1], line[1:-2]
        if kind == b"-":
            raise ProtocolError(value.decode("utf-8", "replace")[:300])
        if kind == b"+":
            return value.decode("utf-8")
        if kind == b":":
            return int(value)
        if kind == b"$":
            size = int(value)
            if size == -1:
                return None
            if not 0 <= size <= 8 * 1024 * 1024:
                raise ProtocolError("RESP bulk limit")
            chunks, remaining = [], size + 2
            while remaining:
                part = stream.read(remaining)
                if not part:
                    raise ProtocolError("Truncated RESP bulk")
                chunks.append(part)
                remaining -= len(part)
            data = b"".join(chunks)
            if not data.endswith(b"\r\n"):
                raise ProtocolError("Invalid RESP bulk terminator")
            return data[:-2].decode("utf-8")
        if kind == b"*":
            count = int(value)
            if count == -1:
                return None
            if not 0 <= count <= 20000:
                raise ProtocolError("RESP array limit")
            return [cls.decode(stream, depth + 1) for unused in range(count)]
        raise ProtocolError("Unknown RESP type")

    def call(self, *args, **kwargs):
        db = kwargs.get("db", 0)
        with socket.create_connection((self.host, self.port), timeout=10) as connection:
            with connection.makefile("rb") as stream:
                if self.password:
                    auth = ("AUTH", self.username, self.password) if self.username else ("AUTH", self.password)
                    connection.sendall(self.encode(auth))
                    self.decode(stream)
                if db:
                    connection.sendall(self.encode(("SELECT", db)))
                    self.decode(stream)
                connection.sendall(self.encode(args))
                return self.decode(stream)
