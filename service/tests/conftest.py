"""No test may accidentally call provider, issuer or payment networks.

AF_UNIX remains available for explicit disposable PostgreSQL and in-process IPC.
Native libpq connections are constrained here as well as in the DB fixture.
"""
import socket
import pytest


@pytest.fixture(autouse=True)
def isolate_external_network(monkeypatch):
    connect=socket.socket.connect
    connect_ex=socket.socket.connect_ex
    sendto=socket.socket.sendto
    def deny(*args,**kwargs):
        raise AssertionError('external network forbidden in service tests')
    def guarded_connect(self,address):
        if self.family!=socket.AF_UNIX:deny()
        return connect(self,address)
    def guarded_connect_ex(self,address):
        if self.family!=socket.AF_UNIX:deny()
        return connect_ex(self,address)
    def guarded_sendto(self,*args):
        if self.family!=socket.AF_UNIX:deny()
        return sendto(self,*args)
    monkeypatch.setattr(socket,'getaddrinfo',deny)
    monkeypatch.setattr(socket.socket,'connect',guarded_connect)
    monkeypatch.setattr(socket.socket,'connect_ex',guarded_connect_ex)
    monkeypatch.setattr(socket.socket,'sendto',guarded_sendto)

    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    database_connect=psycopg.connect
    # row_factory and autocommit are psycopg options, not connection-string fields.
    def guarded_database(conninfo='', **kwargs):
        host=kwargs.get('host',conninfo_to_dict(conninfo).get('host',''))
        if not host.startswith('/') or ',' in host:
            raise AssertionError('external database network forbidden in service tests')
        return database_connect(conninfo, **kwargs)
    monkeypatch.setattr(psycopg,'connect',guarded_database)
