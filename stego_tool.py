import argparse
import json
import math
import os
import struct
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


MAGIC = b"STEG"
VERSION = 1
HEADER_SIZE = 4 + 1 + 2 + 8


class StegoError(Exception):
    pass


def _bytes_to_bits(data):
    for byte in data:
        for shift in range(7, -1, -1):
            yield (byte >> shift) & 1


def _bits_to_bytes(bits):
    output = bytearray()
    for index in range(0, len(bits), 8):
        value = 0
        for bit in bits[index:index + 8]:
            value = (value << 1) | bit
        output.append(value)
    return bytes(output)


def _get_pixels(image):
    if hasattr(image, "get_flattened_data"):
        return list(image.get_flattened_data())
    return list(image.getdata())


def _build_payload(secret_path):
    secret_path = Path(secret_path)
    name_bytes = secret_path.name.encode("utf-8")
    if len(name_bytes) > 65535:
        raise StegoError("Secret file name is too long.")

    secret_data = secret_path.read_bytes()
    header = (
        MAGIC
        + bytes([VERSION])
        + struct.pack(">H", len(name_bytes))
        + struct.pack(">Q", len(secret_data))
        + name_bytes
    )
    return header + secret_data


def _read_exact_lsb_bits(pixels, channel_count, bit_count):
    bits = []
    for pixel in pixels:
        values = pixel if isinstance(pixel, tuple) else (pixel,)
        for channel in range(min(3, channel_count)):
            bits.append(values[channel] & 1)
            if len(bits) == bit_count:
                return bits
    raise StegoError("Stego image does not contain enough data.")


def capacity_bytes(image_path):
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        total_bits = rgb.width * rgb.height * 3
        return total_bits // 8


def hide_file(cover_path, secret_path, output_path):
    payload = _build_payload(secret_path)
    cover = Image.open(cover_path).convert("RGB")
    max_bytes = capacity_bytes(cover_path)

    if len(payload) > max_bytes:
        raise StegoError(
            f"Secret is too large. Need {len(payload)} bytes, but cover can store about {max_bytes} bytes."
        )

    pixels = _get_pixels(cover)
    bit_stream = iter(_bytes_to_bits(payload))
    new_pixels = []
    changed_bits = 0

    for pixel in pixels:
        rgb = list(pixel)
        for channel in range(3):
            try:
                bit = next(bit_stream)
            except StopIteration:
                new_pixels.append(tuple(rgb))
                new_pixels.extend(pixels[len(new_pixels):])
                stego = Image.new("RGB", cover.size)
                stego.putdata(new_pixels)
                stego.save(output_path, "PNG")
                return len(payload), changed_bits

            old_value = rgb[channel]
            rgb[channel] = (old_value & 0xFE) | bit
            if rgb[channel] != old_value:
                changed_bits += 1
        new_pixels.append(tuple(rgb))

    stego = Image.new("RGB", cover.size)
    stego.putdata(new_pixels)
    stego.save(output_path, "PNG")
    return len(payload), changed_bits


def extract_file(stego_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(stego_path) as img:
        stego = img.convert("RGB")
        pixels = _get_pixels(stego)

    base_header_bits = _read_exact_lsb_bits(pixels, 3, HEADER_SIZE * 8)
    base_header = _bits_to_bytes(base_header_bits)

    if base_header[:4] != MAGIC:
        raise StegoError("This image does not look like it was created by this steganography tool.")
    if base_header[4] != VERSION:
        raise StegoError(f"Unsupported stego version: {base_header[4]}")

    name_len = struct.unpack(">H", base_header[5:7])[0]
    data_len = struct.unpack(">Q", base_header[7:15])[0]
    total_len = HEADER_SIZE + name_len + data_len

    all_bits = _read_exact_lsb_bits(pixels, 3, total_len * 8)
    payload = _bits_to_bytes(all_bits)
    name = payload[HEADER_SIZE:HEADER_SIZE + name_len].decode("utf-8")
    data_start = HEADER_SIZE + name_len
    data = payload[data_start:data_start + data_len]

    output_path = output_dir / name
    output_path.write_bytes(data)
    return output_path


def compare_images(cover_path, stego_path):
    cover = Image.open(cover_path).convert("RGB")
    stego = Image.open(stego_path).convert("RGB")
    if cover.size != stego.size:
        raise StegoError("Cover and stego images must have the same dimensions.")

    diff = ImageChops.difference(cover, stego)
    histogram = diff.histogram()
    sq_sum = sum(value * ((index % 256) ** 2) for index, value in enumerate(histogram))
    mse = sq_sum / float(cover.width * cover.height * 3)
    psnr = float("inf") if mse == 0 else 20 * math.log10(255.0 / math.sqrt(mse))
    changed_pixels = sum(1 for pixel in _get_pixels(diff) if pixel != (0, 0, 0))

    return {
        "cover_size_bytes": os.path.getsize(cover_path),
        "stego_size_bytes": os.path.getsize(stego_path),
        "width": cover.width,
        "height": cover.height,
        "changed_pixels": changed_pixels,
        "total_pixels": cover.width * cover.height,
        "mse": mse,
        "psnr_db": psnr,
        "visible_difference": "Usually no visible difference with LSB steganography because only the last bit of color values changes.",
    }


def write_histogram(cover_path, stego_path, output_path):
    cover = Image.open(cover_path).convert("RGB")
    stego = Image.open(stego_path).convert("RGB")
    chart_width = 960
    chart_height = 720
    margin_left = 70
    margin_right = 30
    margin_top = 55
    panel_gap = 45
    panel_height = 175
    plot_width = chart_width - margin_left - margin_right
    colors = ((220, 50, 50), (40, 150, 75), (45, 95, 210))
    labels = ("Red channel", "Green channel", "Blue channel")

    chart = Image.new("RGB", (chart_width, chart_height), "white")
    draw = ImageDraw.Draw(chart)
    draw.text((margin_left, 18), "Cover Image vs Stego Image Histogram", fill=(20, 20, 20))

    cover_hist_full = cover.histogram()
    stego_hist_full = stego.histogram()

    for channel in range(3):
        top = margin_top + channel * (panel_height + panel_gap)
        left = margin_left
        bottom = top + panel_height
        right = left + plot_width

        cover_hist = cover_hist_full[channel * 256:(channel + 1) * 256]
        stego_hist = stego_hist_full[channel * 256:(channel + 1) * 256]
        max_value = max(max(cover_hist), max(stego_hist), 1)
        channel_color = colors[channel]

        draw.rectangle((left, top, right, bottom), outline=(190, 190, 190))
        draw.text((left, top - 22), labels[channel], fill=(20, 20, 20))
        draw.text((right - 210, top - 22), "solid: cover   dotted: stego", fill=(80, 80, 80))

        cover_points = []
        stego_points = []
        for value in range(256):
            x = left + int(value * (plot_width - 1) / 255)
            cover_y = bottom - int((cover_hist[value] / max_value) * (panel_height - 1))
            stego_y = bottom - int((stego_hist[value] / max_value) * (panel_height - 1))
            cover_points.append((x, cover_y))
            stego_points.append((x, stego_y))

        draw.line(cover_points, fill=channel_color, width=2)
        for index in range(0, len(stego_points) - 1, 4):
            draw.line(stego_points[index:index + 2], fill=(40, 40, 40), width=1)

        draw.text((12, top + 5), str(max_value), fill=(100, 100, 100))
        draw.text((35, bottom - 12), "0", fill=(100, 100, 100))
        draw.text((left, bottom + 6), "0", fill=(100, 100, 100))
        draw.text((right - 24, bottom + 6), "255", fill=(100, 100, 100))

    chart.save(output_path, "PNG")


def main():
    parser = argparse.ArgumentParser(description="LSB steganography tool for hiding files inside PNG images.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hide = subparsers.add_parser("hide", help="Hide a secret file inside a cover image.")
    hide.add_argument("--cover", required=True, help="Path to the cover image.")
    hide.add_argument("--secret", required=True, help="Path to the secret file.")
    hide.add_argument("--output", default="stego.png", help="Output stego image path.")

    extract = subparsers.add_parser("extract", help="Extract a secret file from a stego image.")
    extract.add_argument("--stego", required=True, help="Path to the stego image.")
    extract.add_argument("--output-dir", default="extracted", help="Folder where the secret file will be restored.")

    analyze = subparsers.add_parser("analyze", help="Compare cover and stego image quality.")
    analyze.add_argument("--cover", required=True, help="Path to the cover image.")
    analyze.add_argument("--stego", required=True, help="Path to the stego image.")
    analyze.add_argument("--histogram", default="histogram_comparison.png", help="Output histogram image.")
    analyze.add_argument("--json", default=None, help="Optional JSON statistics output path.")

    capacity = subparsers.add_parser("capacity", help="Show how much data a cover image can store.")
    capacity.add_argument("--cover", required=True, help="Path to the cover image.")

    args = parser.parse_args()

    try:
        if args.command == "hide":
            payload_bytes, changed_bits = hide_file(args.cover, args.secret, args.output)
            print(f"Created stego image: {args.output}")
            print(f"Hidden payload size: {payload_bytes} bytes")
            print(f"Changed color values: {changed_bits}")
        elif args.command == "extract":
            output = extract_file(args.stego, args.output_dir)
            print(f"Extracted secret file: {output}")
        elif args.command == "analyze":
            write_histogram(args.cover, args.stego, args.histogram)
            stats = compare_images(args.cover, args.stego)
            if args.json:
                Path(args.json).write_text(json.dumps(stats, indent=2), encoding="utf-8")
            print(f"Created histogram: {args.histogram}")
            print(json.dumps(stats, indent=2))
        elif args.command == "capacity":
            print(f"Approximate capacity: {capacity_bytes(args.cover)} bytes")
    except (OSError, StegoError) as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
