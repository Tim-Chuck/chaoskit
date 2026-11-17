from uvicorn.protocols.http.h11_impl import H11Protocol


class FaultProtocol(H11Protocol):
    """Expose a request-scoped abort hook before the ASGI task starts."""

    def handle_events(self):
        previous = self.scope
        super().handle_events()
        if self.scope is not None and self.scope is not previous:
            cycle = self.cycle

            def terminate():
                cycle.disconnected = True
                cycle.transport.abort()

            self.scope["extensions"] = {"chaoskit.abort": terminate}
