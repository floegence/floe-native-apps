"""Read-only native X11 resource identity and focus; never an input injector.

The compositor supplies exact XIDs. XRes identifies their local client and the
process supervisor verifies live ownership. No window title or PID property is
an authority. The helper owns this private display connection and its lifetime.
"""


class X11Resources:
    def __init__(self, connection):
        from Xlib.error import XError, ConnectionClosedError
        self.connection = connection
        self.errors = (XError, ConnectionClosedError, OSError, ValueError)

    def owner_pid(self, xid):
        from Xlib.ext import res
        try:
            # XRes identifies an allocation range even after an individual
            # resource has disappeared. Require that this window still exists.
            self.connection.create_resource_object('window', xid).query_tree()
            reply = self.connection.res_query_client_ids([{'client': xid, 'mask': res.LocalClientPIDMask}])
            if len(reply.ids) == 1 and reply.ids[0].spec.mask == res.LocalClientPIDMask and len(reply.ids[0].value) == 1:
                pid = int(reply.ids[0].value[0])
                if 1 < pid <= 0x7fffffff:
                    return pid
        except self.errors:
            pass
        return None

    def focused_within(self, xid):
        try:
            focus = self.connection.get_input_focus().focus
            child = focus.id if hasattr(focus, 'id') else focus
            for _ in range(64):
                if child == xid:
                    return True
                if child <= 1:
                    return False
                child = self.connection.create_resource_object('window', child).query_tree().parent.id
        except self.errors:
            pass
        return False

    def close(self):
        self.connection.close()
