import argparse
import socket
import struct

import miniaudio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--host", default="192.168.0.101")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port), timeout=15) as sock:
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        format_data = b""
        while len(format_data) < 4:
            chunk = sock.recv(4 - len(format_data))
            if not chunk:
                raise ConnectionError("S3 closed connection before sending PCM format")
            format_data += chunk
        sample_rate = struct.unpack("<I", format_data)[0]
        stream = miniaudio.stream_file(
            args.file,
            output_format=miniaudio.SampleFormat.UNSIGNED8,
            nchannels=1,
            sample_rate=sample_rate,
            frames_to_read=1024,
        )
        sent = 0
        bytes_per_second = sample_rate
        print(f"Streaming {args.file} to {args.host}:{args.port} at {sample_rate} Hz U8 mono", flush=True)
        for samples in stream:
            payload = samples.tobytes()
            sock.sendall(payload)
            sent += len(payload)
            if sent % (bytes_per_second * 10) < len(payload):
                print(f"{sent / bytes_per_second:.0f} s sent", flush=True)
    print("Finished", flush=True)


if __name__ == "__main__":
    main()
