class AuditRequestMiddleware:
    """
    Stashes the current request on a thread-local-like attribute so model
    save()/delete() signals elsewhere (billing, customers, mikrotik) can
    cheaply pull the acting user + IP without threading them through every
    function signature. Kept intentionally minimal for Phase 1.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response
