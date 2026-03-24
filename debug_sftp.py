"""Diagnostic script - run on the bot's machine to debug SFTP connection."""
import asyncio
import asyncssh
import logging
import sys

# Enable verbose asyncssh logging
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
asyncssh.set_log_level(logging.DEBUG)

HOST = "82.65.116.168"
PORT = 2224
USERNAME = "admin"
PASSWORD = "IcVAdw2w!dhE^h9QXoAJ"

async def test():
    print(f"\n=== Testing SFTP connection to {HOST}:{PORT} ===\n")

    # Test 1: Raw TCP
    print("--- Step 1: Raw TCP connection ---")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT), timeout=10
        )
        data = await asyncio.wait_for(reader.read(256), timeout=5)
        print(f"TCP OK. Server banner: {data[:200]}")
        writer.close()
    except Exception as e:
        print(f"TCP FAILED: {type(e).__name__}: {e}")
        print("Server is unreachable - check firewall/network")
        return

    # Test 2: asyncssh with debug
    print("\n--- Step 2: asyncssh connection ---")
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host=HOST,
                port=PORT,
                username=USERNAME,
                password=PASSWORD,
                known_hosts=None,
                connect_timeout=15,
            ),
            timeout=20
        )
        print("SSH OK!")

        sftp = await conn.start_sftp_client()
        print("SFTP OK!")

        files = await sftp.listdir(".")
        print(f"Files in root: {files[:10]}")

        conn.close()
        await conn.wait_closed()
        print("\nSUCCESS - connection works!")

    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test())
