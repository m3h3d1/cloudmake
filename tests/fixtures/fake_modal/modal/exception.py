class Error(Exception):
    pass


class NotFoundError(Error):
    pass


class AuthError(Error):
    pass


class ConnectionError(Error):
    pass


class ResourceExhaustedError(Error):
    pass


class SandboxTerminatedError(Error):
    pass


class SandboxFilesystemNotFoundError(Error):
    pass
