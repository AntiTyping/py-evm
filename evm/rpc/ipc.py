import asyncio
import json
import os
import traceback

from cytoolz import curry
from eth_utils import decode_hex

from evm import MainnetTesterChain
from evm.db.backends.memory import MemoryDB
from evm.db.chain import BaseChainDB
from evm.rpc.main import RPCServer


MAXIMUM_REQUEST_BYTES = 10000


@curry
async def connection_handler(execute_rpc, reader, writer):
    '''
    Catch fatal errors, log them, and close the connection
    '''
    try:
        await connection_loop(execute_rpc, reader, writer)
    except (ConnectionResetError, asyncio.IncompleteReadError):
        # client closed connection
        pass
    except Exception:
        traceback.print_exc()
        print(
            "What's really going to bake your noodle later on is,",
            "would you still have broken it if I hadn't said anything?",
        )


async def connection_loop(execute_rpc, reader, writer):
    raw_request = ''
    while True:
        request_bytes = b''
        try:
            request_bytes = await reader.readuntil(b'}')
        except asyncio.LimitOverrunError as e:
            request_bytes = await reader.read(e.consumed)
            await write_error(writer, "reached limit: %d bytes, starting with '%s'" % (
                e.consumed,
                request_bytes[:20],
            ))
            continue

        raw_request += request_bytes.decode()

        bad_prefix, raw_request = strip_non_json_prefix(raw_request)
        if bad_prefix:
            await write_error(writer, 'Cannot parse json: ' + bad_prefix)

        try:
            request = json.loads(raw_request)
        except json.JSONDecodeError:
            # invalid json request, keep reading data until a valid json is formed
            continue

        # reset the buffer for the next message
        raw_request = ''

        if not request:
            continue

        try:
            result = execute_rpc(request)
        except Exception as e:
            await write_error(writer, "unknown failure: " + str(e))
        else:
            writer.write(result.encode())

        await writer.drain()


def strip_non_json_prefix(raw_request):
    if raw_request and raw_request[0] != '{':
        prefix, bracket, rest = raw_request.partition('{')
        return prefix.strip(), bracket + rest
    else:
        return '', raw_request


async def write_error(writer, message):
    json_error = '{"error": "%s"}\n' % message
    writer.write(json_error.encode())
    await writer.drain()


def start(path, chain):
    loop = asyncio.get_event_loop()
    rpc = RPCServer(chain)
    loop.run_until_complete(asyncio.start_unix_server(
        connection_handler(rpc.execute),
        path,
        loop=loop,
        limit=MAXIMUM_REQUEST_BYTES,
    ))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print('Tank, I need an exit!')
    finally:
        loop.close()


def get_test_chain():
    root_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..')
    db_path = os.path.join(root_path, 'tests', 'fixtures', 'rpc_test_chain.db')
    db = MemoryDB()
    with open(db_path) as f:
        key_val_hex = json.loads(f.read())
        db.kv_store = {decode_hex(k): decode_hex(v) for k, v in key_val_hex.items()}
    chain_db = BaseChainDB(db)
    return MainnetTesterChain(chain_db)


if __name__ == '__main__':
    start('/tmp/test.ipc', get_test_chain())