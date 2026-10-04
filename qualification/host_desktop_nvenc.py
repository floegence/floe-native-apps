"""Opt-in NVIDIA real-driver qualification, never part of source CI.

Requires a prepared helper and existing FFmpeg only in the test environment.
The worker does not depend on FFmpeg. Decode verifies pixels and bitstream
restrictions; frame throughput is not client presentation latency.
"""
import argparse
import json
import statistics
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--helper', required=True)
args = parser.parse_args()
sys.path.insert(0, args.helper)
from host_desktop_nvenc import NVEncoder

reports = []
for width, height in ((1920, 1080), (3840, 2160)):
    encoder = NVEncoder(width, height, 60, 24000000)
    pixels = bytes([16, 80, 160, 255]) * (width * height)
    durations, access_units = [], []
    try:
        for index in range(120):
            started = time.monotonic()
            data = encoder.encode(pixels)
            durations.append(1000 * (time.monotonic() - started))
            if index < 3:
                access_units.append(data)
    finally:
        encoder.close()
    trace = subprocess.run(['ffmpeg', '-hide_banner', '-i', 'pipe:0', '-c', 'copy',
        '-bsf:v', 'trace_headers', '-f', 'null', '-'], input=b''.join(access_units),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stderr.decode()
    restrictions = [line for line in trace.splitlines() if 'max_num_reorder_frames' in line]
    if not restrictions or not all(line.endswith('= 0') for line in restrictions):
        raise AssertionError('decoder must receive explicit zero-reorder SPS')
    decoded = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'h264',
        '-i', 'pipe:0', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'bgra', 'pipe:1'],
        input=b''.join(access_units), stdout=subprocess.PIPE, check=True).stdout
    center = (height // 2 * width + width // 2) * 4
    if len(decoded) != len(pixels) or any(abs(decoded[center + i] - pixels[center + i]) > 5 for i in range(3)):
        raise AssertionError('NVENC color/layout round trip failed')
    samples = sorted(durations[3:])
    reports.append({'width': width, 'height': height, 'samples': len(samples),
        'pipe_upload_encode_ms_p50': statistics.median(samples),
        'pipe_upload_encode_ms_p95': samples[int(len(samples) * .95)],
        'zero_reorder_sps': True, 'decoded_bgra': list(decoded[center:center + 4])})
print(json.dumps({'measurement': 'synthetic worker round trip, not desktop latency', 'reports': reports}))
