"""Small RESP2 client, avoiding a pip dependency on legacy test hosts."""
import socket
import ssl


class RedisError(RuntimeError):
    pass


class Redis:
    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def encode(arguments):
        values = [str(value).encode("utf-8") for value in arguments]
        return b"*%d\r\n" % len(values) + b"".join(
            b"$%d\r\n" % len(value) + value + b"\r\n" for value in values)

    @classmethod
    def decode(cls, stream):
        line = stream.readline(1_048_577)
        if not line or not line.endswith(b"\r\n"):
            raise RedisError("Incomplete Redis response")
        kind, value = line[:1], line[1:-2]
        if kind == b"-":
            raise RedisError(value.decode("utf-8", "replace"))
        if kind == b"+":
            return value.decode("utf-8")
        if kind == b":":
            return int(value)
        if kind == b"$":
            size = int(value)
            if size == -1:
                return None
            if size < 0 or size > 32 * 1024 * 1024:
                raise RedisError("Invalid Redis bulk response length")
            data = stream.read(size + 2)
            if len(data) != size + 2 or not data.endswith(b"\r\n"):
                raise RedisError("Truncated Redis bulk response")
            return data[:-2].decode("utf-8")
        if kind == b"*":
            count = int(value)
            if count == -1:
                return None
            if count < 0 or count > 100_000:
                raise RedisError("Invalid Redis array length")
            return [cls.decode(stream) for _ in range(count)]
        raise RedisError("Unsupported Redis response")

    def execute(self, *arguments):
        settings = self.settings
        connection = socket.create_connection((settings["host"], int(settings.get("port", 6380))),
                                              timeout=float(settings.get("timeout", 5)))
        try:
            if settings.get("tls"):
                context = ssl.create_default_context(cafile=settings.get("ca_file"))
                connection = context.wrap_socket(connection, server_hostname=settings["host"])
            with connection.makefile("rb") as stream:
                password = settings.get("password")
                if not password:
                    raise RedisError("Redis authentication is required")
                auth = ("AUTH", settings.get("username", "admin"), password)
                connection.sendall(self.encode(auth))
                self.decode(stream)
                connection.sendall(self.encode(arguments))
                return self.decode(stream)
        finally:
            connection.close()
