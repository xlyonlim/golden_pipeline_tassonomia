from pathlib import Path
import hashlib
import re
import shutil


SOURCE_DIR = Path(r"C:\Users\itsci\Documents\Tirocinio\ScriptDataset\Atti raccolti\DelibereGiunta")
DEST_DIR = Path(r"C:\Users\itsci\Documents\Tirocinio\ScriptDataset\Input")
PREFIX = "atto_"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_numbers(dest_dir: Path) -> set[int]:
    pattern = re.compile(rf"^{re.escape(PREFIX)}(\d+)\b", re.IGNORECASE)
    numbers = set()

    for path in dest_dir.iterdir():
        if not path.is_file():
            continue

        match = pattern.match(path.stem)
        if match:
            numbers.add(int(match.group(1)))

    return numbers


def next_free_number(start: int, occupied: set[int]) -> int:
    number = start
    while number in occupied:
        number += 1
    return number


def ask_start_number(suggested: int) -> int:
    while True:
        answer = input(f"Da che numero vuoi partire? [Invio = {suggested}]: ").strip()
        if not answer:
            return suggested

        try:
            number = int(answer)
        except ValueError:
            print("Inserisci un numero intero, per esempio 41.")
            continue

        if number < 1:
            print("Il numero deve essere maggiore o uguale a 1.")
            continue

        return number


def main() -> None:
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Cartella sorgente non trovata: {SOURCE_DIR}")

    DEST_DIR.mkdir(parents=True, exist_ok=True)

    source_files = sorted(
        (path for path in SOURCE_DIR.iterdir() if path.is_file()),
        key=lambda path: path.name.lower(),
    )

    if not source_files:
        print(f"Nessun file trovato in: {SOURCE_DIR}")
        return

    occupied_numbers = existing_numbers(DEST_DIR)
    imported_hashes = {
        file_hash(path)
        for path in DEST_DIR.iterdir()
        if path.is_file()
    }

    suggested_start = (max(occupied_numbers) + 1) if occupied_numbers else 1
    current_number = ask_start_number(suggested_start)

    copied = 0
    skipped = 0

    for source_path in source_files:
        source_digest = file_hash(source_path)
        if source_digest in imported_hashes:
            print(f"Gia presente, salto: {source_path.name}")
            skipped += 1
            continue

        current_number = next_free_number(current_number, occupied_numbers)
        destination_path = DEST_DIR / f"{PREFIX}{current_number}{source_path.suffix.lower()}"

        shutil.copy2(source_path, destination_path)
        imported_hashes.add(source_digest)
        occupied_numbers.add(current_number)

        print(f"Copiato: {source_path.name} -> {destination_path.name}")
        copied += 1
        current_number += 1

    print()
    print(f"Operazione completata. Copiati: {copied}. Saltati perche gia presenti: {skipped}.")


if __name__ == "__main__":
    main()
