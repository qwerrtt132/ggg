"""The address other devices on this network can use to reach this PC."""

import socket


def lan_address():
    """Returns this PC's address on the local network, or None when there is no network. No packets are sent:
    connecting a UDP socket only asks the system which interface it would use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # a documentation address; nothing is sent to it
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address
