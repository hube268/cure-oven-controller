"""
test_serial.py — Serial Initialization Diagnostic
Tries different serial port opening strategies to find one that
gets data from the R4 Minima on the Orange Pi / RK3566.
Run: python3 test_serial.py
"""

import serial
import time
import sys

PORT   = "/dev/ttyACM0"
BAUD   = 115200
WAIT   = 4      # seconds to wait for data per attempt
CMDS   = [None, b"\n", b"?\n", b"Status\n", b"WarmUp\n"]


def try_read(p, label, send=None):
    """Flush, optionally send a command, read for WAIT seconds."""
    try:
        p.reset_input_buffer()
        if send:
            p.write(send)
        deadline = time.time() + WAIT
        buf = b""
        while time.time() < deadline:
            chunk = p.read(p.in_waiting or 1)
            if chunk:
                buf += chunk
                if b"\n" in buf:
                    break
        if buf:
            print(f"  [GOT DATA] {label}: {repr(buf[:120])}")
            return True
        else:
            print(f"  [nothing]  {label}")
            return False
    except Exception as e:
        print(f"  [error]    {label}: {e}")
        return False


def attempt(label, **kwargs):
    print(f"\n{'='*50}")
    print(f"Attempt: {label}")
    try:
        p = serial.Serial(PORT, BAUD, timeout=0.5, **kwargs)
        time.sleep(2.5)
        for cmd in CMDS:
            tag = f"send={repr(cmd)}"
            if try_read(p, tag, send=cmd):
                p.close()
                return True
        p.close()
    except Exception as e:
        print(f"  [open error] {e}")
    return False


def main():
    print(f"Serial diagnostic on {PORT} @ {BAUD}")
    print("Each attempt opens the port differently and waits for data.\n")

    strategies = [
        ("Default (pyserial defaults)",     {}),
        ("DSR/DTR disabled",                {"dsrdtr": False, "rtscts": False}),
        ("DTR=False on open",               {"dsrdtr": False}),
        ("RTS/CTS enabled",                 {"rtscts": True}),
        ("XON/XOFF flow",                   {"xonxoff": True}),
        ("No flow, long timeout",           {"dsrdtr": False, "rtscts": False, "timeout": 2.0}),
    ]

    for label, kwargs in strategies:
        if attempt(label, **kwargs):
            print(f"\n*** SUCCESS with: {label} ***")
            print("Add these kwargs to serial.Serial() in grbl_serial.py")
            sys.exit(0)

    # Also try explicit DTR toggle
    print(f"\n{'='*50}")
    print("Attempt: Manual DTR toggle sequence")
    try:
        p = serial.Serial()
        p.port     = PORT
        p.baudrate = BAUD
        p.timeout  = 0.5
        p.dtr      = False
        p.rts      = False
        p.open()
        time.sleep(0.5)
        p.dtr = True
        time.sleep(2)
        for cmd in CMDS:
            tag = f"after DTR toggle, send={repr(cmd)}"
            if try_read(p, tag, send=cmd):
                print("\n*** SUCCESS: DTR toggle triggered data ***")
                p.close()
                sys.exit(0)
        p.close()
    except Exception as e:
        print(f"  [error] {e}")

    print("\n\nNo strategy produced data from the PCB.")
    print("Check: is the oven powered on and the PCB running?")


if __name__ == "__main__":
    main()
