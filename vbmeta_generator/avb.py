import os
import re
import tempfile
from pathlib import Path

from vbmeta_generator import utils


def generate_key(key_path: str, algorithm: str = "SHA256_RSA2048") -> str:
    bits = {"SHA256_RSA2048": 2048, "SHA256_RSA4096": 4096, "SHA512_RSA4096": 4096}.get(algorithm, 2048)
    pubkey_path = str(Path(key_path).with_suffix(".avbpubkey"))

    utils.run("openssl", "genpkey", "-algorithm", "RSA",
              "-pkeyopt", f"rsa_keygen_bits:{bits}",
              "-out", key_path, check=True)
    utils.run("avbtool", "extract_public_key",
              "--key", key_path, "--output", pubkey_path, check=True)
    return pubkey_path


def add_hash_footer(image_path: str, partition_name: str, key_path: str,
                    algorithm: str, rollback_index: int = 1,
                    partition_size: int | None = None,
                    props: list[tuple[str, str, str]] | None = None) -> None:
    cmd = ["avbtool", "add_hash_footer",
           "--image", image_path,
           "--partition_name", partition_name,
           "--key", key_path,
           "--algorithm", algorithm,
           "--rollback_index", str(rollback_index)]

    if partition_size:
        cmd.extend(["--partition_size", str(partition_size)])

    if props:
        for ptype, key, val in props:
            cmd.extend([f"--{ptype}", f"{key}:{val}"])

    result = utils.run(*cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"add_hash_footer failed for {partition_name}:\n{result.stderr}")


def add_hashtree_footer(image_path: str, partition_name: str, key_path: str,
                        algorithm: str, rollback_index: int = 1,
                        partition_size: int | None = None,
                        hash_algorithm: str = "sha1",
                        props: list[tuple[str, str, str]] | None = None,
                        do_not_append_vbmeta: bool = False,
                        output_vbmeta: str | None = None) -> None:
    cmd = ["avbtool", "add_hashtree_footer",
           "--image", image_path,
           "--partition_name", partition_name,
           "--key", key_path,
           "--algorithm", algorithm,
           "--hash_algorithm", hash_algorithm,
           "--rollback_index", str(rollback_index)]

    if partition_size:
        cmd.extend(["--partition_size", str(partition_size)])
    if do_not_append_vbmeta:
        cmd.append("--do_not_append_vbmeta")
    if output_vbmeta:
        cmd.extend(["--output_vbmeta", output_vbmeta])
    if props:
        for ptype, key, val in props:
            cmd.extend([f"--{ptype}", f"{key}:{val}"])

    result = utils.run(*cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"add_hashtree_footer failed for {partition_name}:\n{result.stderr}")


def make_vbmeta_image(output_path: str, key_path: str, algorithm: str,
                      rollback_index: int = 0, flags: int = 0,
                      padding_size: int = 4096,
                      chain_partitions: list[tuple[str, int, str]] | None = None,
                      include_descriptors_from: list[str] | None = None,
                      props: list[tuple[str, str, str]] | None = None) -> None:
    cmd = ["avbtool", "make_vbmeta_image",
           "--output", output_path,
           "--key", key_path,
           "--algorithm", algorithm,
           "--rollback_index", str(rollback_index),
           "--flags", str(flags),
           "--padding_size", str(padding_size)]

    if chain_partitions:
        for part_name, rollback_loc, pubkey_path in chain_partitions:
            cmd.extend(["--chain_partition",
                       f"{part_name}:{rollback_loc}:{pubkey_path}"])

    if include_descriptors_from:
        for img in include_descriptors_from:
            cmd.extend(["--include_descriptors_from_image", img])

    if props:
        for ptype, key, val in props:
            cmd.extend([f"--{ptype}", f"{key}:{val}"])

    result = utils.run(*cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"make_vbmeta_image failed:\n{result.stderr}")


def info_image(image_path: str) -> str:
    result = utils.run("avbtool", "info_image", "--image", image_path,
                       capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def get_image_size(image_path: str) -> int | None:
    out = info_image(image_path)
    m = re.search(r"Image size:\s+(\d+)", out)
    if m:
        return int(m.group(1))
    return None


def get_public_key_sha1(pubkey_path: str) -> str | None:
    out = info_image(pubkey_path)
    if not out:
        return None
    m = re.search(r"Public key \(sha1\):\s+([a-f0-9]+)", out)
    if m:
        return m.group(1)
    return None


def extract_public_key_digest(key_path: str) -> str:
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt") as tf:
        result = utils.run("avbtool", "extract_public_key_digest",
                           "--key", key_path, "--output", tf.name,
                           capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"extract_public_key_digest failed:\n{result.stderr}")
        tf.seek(0)
        return tf.read().strip()
