# ============================================================
# 01_estrazione_e_segmentazione.py
#
# Obiettivo:
# trasformare output Markdown/JSON da Docling, OpenDataLoader o Marker
# in un dataset di blocchi narrativi etichettati.
#
# ============================================================

from pathlib import Path
import argparse
import hashlib
import html
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd

from document_ids import (
    canonical_document_id,
    infer_dataset_prefix,
    normalize_prefix,
)


# ============================================================
# 1. CONFIGURAZIONE
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_OUTPUT_ROOT = SCRIPT_DIR / Path(__file__).stem
DEFAULT_INPUT_ROOT = SCRIPT_DIR / "Input"
DEFAULT_OUT_ROOT = SCRIPT_OUTPUT_ROOT
SELECTED_TOOL = "docling"
SELECTED_FORMAT = "json"
EXTRACTOR_TOOLS = ("docling", "opendataloader", "marker")


def natural_sort_key(path: Path) -> List[Any]:
    """Ordina i nomi in modo naturale: 2.pdf prima di 10.pdf."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", path.name)]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_input_pdfs(
    input_root: Path,
    preserve_names: bool = False,
    id_prefix: str = "INPUT",
) -> pd.DataFrame:
    """Censisce i PDF e li rinomina con un identificativo di insieme stabile.

    ``preserve_names`` serve in particolare per un golden set già identificato
    (per esempio ``GOLD_0001.pdf``): non rinomina e non elimina alcun file,
    evitando di rompere il collegamento con ``golden_delibere.csv``.
    """
    id_prefix = normalize_prefix(id_prefix)
    pdfs = sorted(
        (path for path in input_root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=natural_sort_key,
    )
    existing_ids: Dict[Path, str] = {}
    for path in pdfs:
        try:
            existing_id = canonical_document_id(path.stem)
        except ValueError:
            continue
        existing_prefix = existing_id.split("_", 1)[0]
        if existing_prefix != id_prefix:
            raise ValueError(
                f"Il PDF {path.name!r} appartiene allo spazio {existing_prefix}, "
                f"ma la cartella e' configurata come {id_prefix}."
            )
        existing_ids[path] = existing_id

    used_numbers = {
        int(document_id.split("_", 1)[1])
        for document_id in existing_ids.values()
    }
    next_number = max(used_numbers, default=0) + 1
    # Gli ID gia' assegnati hanno precedenza nel controllo dei duplicati.
    pdfs.sort(key=lambda path: (path not in existing_ids, natural_sort_key(path)))

    records = []
    unique_items = []
    first_by_hash: Dict[str, Dict[str, str]] = {}
    for path in pdfs:
        sha256 = file_sha256(path)
        first = first_by_hash.get(sha256)
        if first is None:
            if preserve_names:
                try:
                    id_delibera = canonical_document_id(path.stem)
                except ValueError as exc:
                    raise ValueError(
                        "Con --preserva-nomi-pdf ogni PDF deve chiamarsi "
                        "GOLD_####.pdf o INPUT_####.pdf: "
                        f"nome non valido {path.name!r}"
                    ) from exc
                if id_delibera.split("_", 1)[0] != id_prefix:
                    raise ValueError(
                        f"Il PDF {path.name!r} non appartiene allo spazio {id_prefix}."
                    )
                assigned_name = f"{id_delibera}.pdf"
            elif path in existing_ids:
                id_delibera = existing_ids[path]
                assigned_name = f"{id_delibera}.pdf"
            else:
                while next_number in used_numbers:
                    next_number += 1
                id_delibera = f"{id_prefix}_{next_number:04d}"
                assigned_name = f"{id_delibera}.pdf"
                used_numbers.add(next_number)
                next_number += 1
        else:
            id_delibera = first["id_delibera"]
            assigned_name = None
        record = {
            "nome_originale": path.name,
            "percorso_originale": str(path),
            "sha256": sha256,
            "duplicato": int(first is not None),
            "duplicato_di": first["nome_assegnato"] if first else None,
            "id_delibera": id_delibera,
            "nome_assegnato": assigned_name,
            "eliminato": int(first is not None),
        }

        if first is None:
            first_by_hash[sha256] = record.copy()
            unique_items.append((path, record))
        elif not preserve_names:
            path.unlink()

        records.append(record)

    if preserve_names:
        return pd.DataFrame(records)

    # Due passaggi evitano collisioni se alcuni file hanno già il nome finale.
    rename_items = [
        (path, record)
        for path, record in unique_items
        if path.resolve() != (input_root / record["nome_assegnato"]).resolve()
    ]
    temporary_paths = []
    for path, _ in rename_items:
        temporary = path.with_name(f".__rename_{uuid.uuid4().hex}.pdf")
        path.rename(temporary)
        temporary_paths.append(temporary)

    for temporary, (_, record) in zip(temporary_paths, rename_items):
        temporary.rename(input_root / record["nome_assegnato"])

    return pd.DataFrame(records)


def next_segmentation_dir(output_root: Path) -> Path:
    """Riprende l'ultima esecuzione incompleta o crea SEGMENTAZIONE_N."""
    output_root.mkdir(parents=True, exist_ok=True)
    used_numbers = []
    for path in output_root.iterdir():
        if path.is_dir():
            match = re.fullmatch(r"SEGMENTAZIONE_(\d+)", path.name, flags=re.IGNORECASE)
            if match:
                used_numbers.append(int(match.group(1)))

    if used_numbers:
        latest = output_root / f"SEGMENTAZIONE_{max(used_numbers)}"
        if not (latest / "00_COMPLETATA.txt").exists():
            return latest

    number = max(used_numbers, default=0) + 1
    destination = output_root / f"SEGMENTAZIONE_{number}"
    destination.mkdir(parents=True, exist_ok=False)
    return destination


# ============================================================
# 2. MARKER NARRATIVI / ISTRUTTORI / MOTIVAZIONALI
# ============================================================

MARKERS = {
    # ========================================================
    # STOP: inizio dispositivo
    # ========================================================
    "PROPONE_DELIBERARE": (
        r"(?:si\s+)?propone\s+di\s+deliberare|"
        r"propone\s+.*deliberare\s+quanto\s+segue|"
        r"propone\s+alla\s+g\.?c\.?\s+di\s+deliberare"
    ),

    "DELIBERA": (
        r"\bdelibera\b|"
        r"\bdeliberare\s+quanto\s+segue\b"
    ),

    # ========================================================
    # Premesse
    # ========================================================
    "PREMESSO_E_RILEVATO": (
        r"premess[oaie]\s+(?:ed|e\s+d|e)\s+rilevat[oaie]"
        r"\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "PREMESSO": (
        r"premess[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’])?"
    ),

    # ========================================================
    # Riferimenti / richiami
    # ========================================================
    "VISTI_ED_ACQUISITI": (
        r"vist[oaie]\s+(?:ed|e\s+d|e)\s+acquisit[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|i\s+pareri|il\s+parere)?"
    ),

    "VISTA_E_CONDIVISA": (
        r"vist[oaie]\s+e\s+condivis[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|della|del|dei|degli|delle|dell['’])?"
    ),

    "VISTO_E_PRESO_ATTO": (
        r"vist[oaie]\s+e\s+pres[oaie]\s+atto\s*(?:,|:|;)?\s*"
        r"(?:che|della|del|dei|degli|delle|dell['’]|di|in\s+ordine\s+a)?"
    ),

    "VISTI_PARERI": (
        r"vist[ioe]\s*(?:,|:|;)?\s*"
        r"(?:i\s+)?pareri|"
        r"visto\s+il\s+parere|"
        r"vista\s+la\s+regolarit[aà]"
    ),

    "VISTO": (
        r"vist[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|gli\s+atti|gli\s+articoli|ai\s+sensi)?"
    ),

    "RICHIAMATO": (
        r"richiamat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|altres[iì]|il\s+provvedimento|la\s+deliberazione)?"
    ),

    "RAMMENTATO": (
        r"rammentat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|altres[iì])?"
    ),

    "RICORDATO": (
        r"ricordat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’])?"
    ),

    "RICORDANDO": (
        r"ricordando\b\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’])?"
    ),

    # ========================================================
    # Pareri / attestazioni / acquisizioni
    # ========================================================
    "AI_SENSI": (
        r"a\s*i\s+sensi\b\s*(?:,|:|;)?\s*"
        r"(?:dell['’]?|del|della|dei|degli|delle|di|d['’])?"
    ),

    "ACQUISITI_PARERI": (
        r"acquisit[oaie]\s*(?:,|:|;)?\s*"
        r"(?:sulla\s+(?:predetta\s+)?proposta\s*,?\s*)?"
        r"(?:i\s+)?pareri|"
        r"acquisit[oaie]\s+il\s+parere|"
        r"acquisit[oaie]\s+la\s+regolarit[aà]"
    ),

    "ACQUISITO": (
        r"acquisit[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|agli\s+atti|al\s+protocollo|il\s+parere|i\s+pareri)?"
    ),

    "ATTESTA": (
        r"(?:si\s+)?attest(?:a|ano)\b\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|ai\s+sensi|per)?"
    ),

    "ATTESTATO": (
        r"attestat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|ai\s+sensi|per)?"
    ),

    "RILASCIATO": (
        r"rilasciat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|il\s+parere|la\s+certificazione|"
        r"l['’]autorizzazione|il\s+nulla\s+osta)?"
    ),

    # ========================================================
    # Presupposti / istruttoria
    # ========================================================
    "DATO_ATTO": (
        r"dat[oaie]\s+(?:ancora\s+)?atto\s*(?:,|:|;)?\s*"
        r"(?:altres[iì]\s*,?\s*)?"
        r"(?:che|della|del|dei|degli|delle|dell['’]|in\s+ordine\s+a|relativamente\s+a)?"
    ),

    "FATTO_PROPRIO": (
        r"fatt[oaie]\s+propri(?:o|a|e)?\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|della|del|dei|degli|delle|dell['’])?"
    ),

    "FATTO_PRESENTE": (
        r"fatt(?:o\s+presente|a\s+presente|i\s+presenti|e\s+presenti)"
        r"\s*(?:,|:|;)?\s*(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "PRESA_VISIONE": (
        r"pres[oaie]\s+visione\s*(?:,|:|;)?\s*"
        r"(?:che|della|del|dei|degli|delle|dell['’]|di|la|il|le|gli|i)?"
    ),

    "PRESO_ATTO": (
        r"pres[oaie]\s+atto\s*(?:,|:|;)?\s*"
        r"(?:altres[iì]\s*,?\s*)?"
        r"(?:che|della|del|dei|degli|delle|dell['’]|in\s+ordine\s+a|relativamente\s+a)?"
    ),

    "AVUTO_RIGUARDO": (
        r"avut[oaie]\s+riguardo\s*(?:,|:|;)?\s*"
        r"(?:a|al|alla|ai|agli|alle|all['’]|che|la|il|le|gli|i|quanto)?"
    ),

    "TENUTO_CONTO": (
        r"tenut[oaie]\s+(?:altres[iì]\s+)?conto\s*(?:,|:|;)?\s*"
        r"(?:che|della|del|dei|degli|delle|dell['’]|in\s+ordine\s+a|relativamente\s+a)?"
    ),

    "TENUTO_PRESENTE": (
        r"tenut[oaie]\s+(?:altres[iì]\s+)?present[ei]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|della|del|dei|degli|delle|dell['’])?"
    ),

    "ASSUNTO": (
        r"assunt[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|agli\s+atti|come|quale)?"
    ),

    "UDITO": (
        r"udit[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|la\s+proposta|il\s+relatore)?"
    ),

    "DOPO": (
        r"dopo\b\s*(?:,|:|;)?\s*"
        r"(?:che|aver|avere|ampia|approfondita|breve|la|il|lo|le|gli|i|l['’])?"
    ),

    "SU_INVITO": (
        r"su\s+invito\s*(?:,|:|;)?\s*"
        r"(?:del|della|di|dei|degli|delle|dell['’]|al|alla)?"
    ),

    "ADEMPIUTO": (
        r"adempiut[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|a|al|alla|ai|agli|alle|all['’]|il|la|i|gli|le|quanto)?"
    ),

    "ACCERTATO": (
        r"accertat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "CONSTATATO": (
        r"constatat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "APPURATO": (
        r"appurat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "RISCONTRATO": (
        r"riscontrat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "AVVISATO": (
        r"avvisat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "VERIFICATO": (
        r"verificat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "LETTO_ESAMINATO": (
        r"(?:lett[oa]\s+(?:ed?\s+)?esaminat[oa]|"
        r"letti\s+(?:ed?\s+)?esaminati|"
        r"lette\s+(?:ed?\s+)?esaminate)\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|gli\s+atti|la\s+documentazione|la\s+proposta)?"
    ),

    "LETTO": (
        r"lett[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|gli\s+atti|la\s+documentazione|la\s+proposta)?"
    ),

    "ESAMINATO": (
        r"esaminat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|gli\s+atti|la\s+documentazione|la\s+proposta)?"
    ),

    "ANALIZZATO": (
        r"analizzat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per|attentamente|la\s+documentazione|gli\s+atti)?"
    ),

    "DEPOSITATO": (
        r"depositat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|agli\s+atti|presso|in|al|alla)?"
    ),

    "PREDISPOSTO": (
        r"predispost[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|lo\s+schema|la\s+proposta|gli\s+atti)?"
    ),

    "FORMULATO": (
        r"formulat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|la\s+proposta|il\s+parere|osservazioni)?"
    ),

    "INDIVIDUATO": (
        r"individuat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|nel|nella|nei|nelle)?"
    ),

    "INCARICATO": (
        r"incaricat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    # ========================================================
    # Motivazione / valutazione
    # ========================================================
    "STANTE": (
        r"stante\b\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|quanto|la\s+necessit[aà])?"
    ),

    "CONSIDERATO": (
        r"considerat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "RITENUTO_NECESSARIO": (
        r"ritenut[oaie]\s*(?:,|:|;)?\s*"
        r"(?:(?:altres[iì]|pertanto|quindi)\s*,?\s*)?"
        r"necessari(?:o|a|e)?\b\s*(?:,|:|;)?\s*"
        r"(?:che|di|la|il|lo|le|gli|i|l['’]|procedere|approvare)?"
    ),

    "RITENUTO_OPPORTUNO": (
        r"ritenut[oaie]\s*(?:,|:|;)?\s*"
        r"(?:(?:altres[iì]|pertanto|quindi)\s*,?\s*)?"
        r"opportun[oaie]\b\s*(?:,|:|;)?\s*"
        r"(?:che|di|la|il|lo|le|gli|i|l['’]|procedere|approvare)?"
    ),

    "RITENUTO": (
        r"ritenut[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per|necessari[oaie]|opportun[oaie])?"
    ),

    "ATTESO": (
        r"attes[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "RAVVISATO": (
        r"ravvisat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "RILEVATO": (
        r"rilevat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "OSSERVATO": (
        r"osservat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "POSTO": (
        r"post[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "VALUTATO_OPPORTUNO": (
        r"valutat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:(?:altres[iì]|pertanto|quindi)\s*,?\s*)?"
        r"opportun[oaie]\b\s*(?:,|:|;)?\s*"
        r"(?:che|di|la|il|lo|le|gli|i|l['’]|procedere|approvare)?"
    ),

    "VALUTATO": (
        r"valutat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "EVIDENZIATO": (
        r"evidenziat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per)?"
    ),

    "RIBADITO": (
        r"ribadit[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per|quanto)?"
    ),

    "RICONOSCIUTO": (
        r"riconosciut[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|a|per|necessario|opportuno|i\s+requisiti)?"
    ),

    "SPECIFICATO": (
        r"specificat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|quanto|in\s+merito\s+a)?"
    ),

    "PRECISATO": (
        r"precisat[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|quanto|in\s+merito\s+a)?"
    ),

    "STABILITO": (
        r"stabilit[oaie]\s*(?:,|:|;)?\s*"
        r"(?:che|la|il|lo|le|gli|i|l['’]|di|quanto)?"
    ),
}

STOP_MARKERS = {
    "DELIBERA",
    "PROPONE_DELIBERARE",
    "ATTESTA",
}

MACRO_SECTION = {
    "PREMESSO_E_RILEVATO": "NARRATIVA_PREMESSA_MOTIVAZIONE",
    "PREMESSO": "NARRATIVA_PREMESSA",
    "VISTI_ED_ACQUISITI": "PARERI_ATTESTAZIONI",
    "VISTA_E_CONDIVISA": "ISTRUTTORIA_MOTIVAZIONE",
    "VISTO_E_PRESO_ATTO": "ISTRUTTORIA",
    "VISTO": "PREAMBOLO_RIFERIMENTI",
    "VISTI_PARERI": "PARERI_ATTESTAZIONI",
    "RICHIAMATO": "PREAMBOLO_RIFERIMENTI",
    "RAMMENTATO": "PREAMBOLO_RIFERIMENTI",
    "RICORDATO": "PREAMBOLO_RIFERIMENTI",
    "RICORDANDO": "PREAMBOLO_RIFERIMENTI",
    "AI_SENSI": "PREAMBOLO_RIFERIMENTI",
    "ACQUISITI_PARERI": "PARERI_ATTESTAZIONI",
    "ACQUISITO": "ISTRUTTORIA",
    "ATTESTA": "PARERI_ATTESTAZIONI",
    "ATTESTATO": "PARERI_ATTESTAZIONI",
    "RILASCIATO": "ISTRUTTORIA_PARERI",
    "DATO_ATTO": "ISTRUTTORIA",
    "FATTO_PROPRIO": "ISTRUTTORIA",
    "FATTO_PRESENTE": "ISTRUTTORIA",
    "PRESA_VISIONE": "ISTRUTTORIA",
    "PRESO_ATTO": "ISTRUTTORIA",
    "AVUTO_RIGUARDO": "ISTRUTTORIA_MOTIVAZIONE",
    "TENUTO_CONTO": "ISTRUTTORIA_MOTIVAZIONE",
    "TENUTO_PRESENTE": "ISTRUTTORIA_MOTIVAZIONE",
    "ASSUNTO": "ISTRUTTORIA",
    "UDITO": "ISTRUTTORIA",
    "DOPO": "ISTRUTTORIA",
    "SU_INVITO": "ISTRUTTORIA",
    "ADEMPIUTO": "ISTRUTTORIA",
    "ACCERTATO": "ISTRUTTORIA",
    "CONSTATATO": "ISTRUTTORIA",
    "APPURATO": "ISTRUTTORIA",
    "RISCONTRATO": "ISTRUTTORIA",
    "AVVISATO": "ISTRUTTORIA",
    "VERIFICATO": "ISTRUTTORIA",
    "LETTO_ESAMINATO": "ISTRUTTORIA_VALUTAZIONE",
    "LETTO": "ISTRUTTORIA",
    "ESAMINATO": "ISTRUTTORIA_VALUTAZIONE",
    "ANALIZZATO": "ISTRUTTORIA_VALUTAZIONE",
    "DEPOSITATO": "ISTRUTTORIA",
    "PREDISPOSTO": "ISTRUTTORIA",
    "FORMULATO": "ISTRUTTORIA",
    "INDIVIDUATO": "ISTRUTTORIA",
    "INCARICATO": "ISTRUTTORIA",
    "STANTE": "MOTIVAZIONE",
    "CONSIDERATO": "MOTIVAZIONE",
    "RITENUTO_NECESSARIO": "MOTIVAZIONE_VALUTAZIONE",
    "RITENUTO_OPPORTUNO": "MOTIVAZIONE_VALUTAZIONE",
    "RITENUTO": "MOTIVAZIONE_VALUTAZIONE",
    "ATTESO": "MOTIVAZIONE",
    "RAVVISATO": "MOTIVAZIONE",
    "RILEVATO": "ISTRUTTORIA_MOTIVAZIONE",
    "OSSERVATO": "ISTRUTTORIA_MOTIVAZIONE",
    "POSTO": "NARRATIVA_PREMESSA",
    "VALUTATO_OPPORTUNO": "MOTIVAZIONE_VALUTAZIONE",
    "VALUTATO": "MOTIVAZIONE_VALUTAZIONE",
    "EVIDENZIATO": "MOTIVAZIONE",
    "RIBADITO": "MOTIVAZIONE",
    "RICONOSCIUTO": "MOTIVAZIONE_VALUTAZIONE",
    "SPECIFICATO": "ISTRUTTORIA_MOTIVAZIONE",
    "PRECISATO": "ISTRUTTORIA_MOTIVAZIONE",
    "STABILITO": "MOTIVAZIONE_VALUTAZIONE",
}


NOISE_PATTERNS = [
    r"documento\s+sottoscritto\s+con\s+firma\s+digitale",
    r"atto\s+sottoscritto\s+digitalmente",
    r"(?:fatto,\s*)?(?:il\s+presente\s+(?:atto|verbale)\s+viene\s+)?"
    r"letto,?\s*(?:approvato|confermato)\s+e\s+sottoscritto",
    r"pubblicazione\s+all['’]albo",
    r"certificato\s+di\s+pubblicazione",
    r"firmato\s+da\s*:.*codice\s+fiscale",
    r"(?:delibera(?:zione)?\s+(?:di|della)\s+giunta|"
    r"delibera\s+g\.?\s*c\.?).*pag\.\s*\d+",
    r"comune\s+di\s+.*[-\u2013]\s*deliberazione\s+n\.\s*\d+.*\b\d+\s*$",
    r"pag\.\s*\d+",
    r"pagina\s+\d+",
    r"presenti\s*:\s*\d+",
    r"assenti\s*:\s*\d+",
]


# ============================================================
# 3. FUNZIONI BASE
# ============================================================

def normalize_text(text: Any) -> str:
    """Normalizzazione leggera: utile per OCR/PDF rumorosi."""
    if text is None:
        return ""

    text = str(text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)

    replacements = {
        "\u00A0": " ",
        "\u2007": " ",
        "\u202F": " ",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "`": "'",
        "´": "'",
        "–": "-",
        "—": "-",
        "•": "-",
        "●": "-",
        "▪": "-",
        "": "-",
        "": "-",
        "\uf0a7": "-",
        "\uf0b7": "-",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = compact_spaced_markers(text)
    text = re.sub(r"\s+", " ", text).strip()


    trailing = re.search(r"([;:.])\s+([^;:.]{1,80})$", text)
    if trailing:
        tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", trailing.group(2))
        single_letters = sum(len(token) == 1 for token in tokens)
        if len(tokens) >= 5 and single_letters >= 3:
            text = text[:trailing.start() + 1].rstrip()

    return text


def compact_spaced_markers(text: str) -> str:
    """
    Compatta marker OCR sporchi:
    V I S T O -> VISTO
    C O N S I D E R A T O -> CONSIDERATO
    """
    keywords = [
        "VISTO", "VISTA", "VISTI", "VISTE",
        "CONDIVISO", "CONDIVISA", "CONDIVISI", "CONDIVISE",
        "CONSIDERATO", "CONSIDERATA", "CONSIDERATI", "CONSIDERATE",
        "PREMESSO", "PREMESSA", "PREMESSI", "PREMESSE",
        "RITENUTO", "RITENUTA", "RITENUTI", "RITENUTE",
        "STANTE",
        "ATTESO", "ATTESA", "ATTESI", "ATTESE",
        "RAVVISATO", "RAVVISATA", "RAVVISATI", "RAVVISATE",
        "RILEVATO", "RILEVATA", "RILEVATI", "RILEVATE",
        "OSSERVATO", "OSSERVATA", "OSSERVATI", "OSSERVATE",
        "POSTO", "POSTA", "POSTI", "POSTE",
        "RICHIAMATO", "RICHIAMATA", "RICHIAMATI", "RICHIAMATE",
        "RAMMENTATO", "RAMMENTATA", "RAMMENTATI", "RAMMENTATE",
        "RICORDATO", "RICORDATA", "RICORDATI", "RICORDATE",
        "RICORDANDO",
        "ACCERTATO", "ACCERTATA", "ACCERTATI", "ACCERTATE",
        "CONSTATATO", "CONSTATATA", "CONSTATATI", "CONSTATATE",
        "APPURATO", "APPURATA", "APPURATI", "APPURATE",
        "RISCONTRATO", "RISCONTRATA", "RISCONTRATI", "RISCONTRATE",
        "AVVISATO", "AVVISATA", "AVVISATI", "AVVISATE",
        "VERIFICATO", "VERIFICATA", "VERIFICATI", "VERIFICATE",
        "VALUTATO", "VALUTATA", "VALUTATI", "VALUTATE",
        "SENSI",
        "EVIDENZIATO", "EVIDENZIATA", "EVIDENZIATI", "EVIDENZIATE",
        "RIBADITO", "RIBADITA", "RIBADITI", "RIBADITE",
        "RICONOSCIUTO", "RICONOSCIUTA", "RICONOSCIUTI", "RICONOSCIUTE",
        "ANALIZZATO", "ANALIZZATA", "ANALIZZATI", "ANALIZZATE",
        "DEPOSITATO", "DEPOSITATA", "DEPOSITATI", "DEPOSITATE",
        "LETTO", "LETTA", "LETTI", "LETTE",
        "ESAMINATO", "ESAMINATA", "ESAMINATI", "ESAMINATE",
        "SPECIFICATO", "SPECIFICATA", "SPECIFICATI", "SPECIFICATE",
        "PRECISATO", "PRECISATA", "PRECISATI", "PRECISATE",
        "FORMULATO", "FORMULATA", "FORMULATI", "FORMULATE",
        "PREDISPOSTO", "PREDISPOSTA", "PREDISPOSTI", "PREDISPOSTE",
        "RILASCIATO", "RILASCIATA", "RILASCIATI", "RILASCIATE",
        "STABILITO", "STABILITA", "STABILITI", "STABILITE",
        "INDIVIDUATO", "INDIVIDUATA", "INDIVIDUATI", "INDIVIDUATE",
        "INCARICATO", "INCARICATA", "INCARICATI", "INCARICATE",
        "ACQUISITO", "ACQUISITA", "ACQUISITI", "ACQUISITE",
        "DATO", "DATA", "DATI", "DATE",
        "FATTO", "FATTA", "FATTI", "FATTE", "PRESENTE", "PRESENTI",
        "PRESO", "PRESA", "PRESI", "PRESE", "VISIONE",
        "TENUTO", "TENUTA", "TENUTI", "TENUTE",
        "AVUTO", "AVUTA", "AVUTI", "AVUTE", "RIGUARDO",
        "ASSUNTO", "ASSUNTA", "ASSUNTI", "ASSUNTE",
        "UDITO", "UDITA", "UDITI", "UDITE",
        "DOPO",
        "ATTO", "CONTO",
        "ATTESTA", "ATTESTANO",
        "ATTESTATO", "ATTESTATA", "ATTESTATI", "ATTESTATE",
        "PROPONE", "PROPONGONO",
        "DELIBERA", "DELIBERANO", "DELIBERARE",
        "DICHIARA", "DICHIARANO", "DICHIARARE",
    ]

    for kw in keywords:
        pattern = r"\b" + r"\s*".join(list(kw)) + r"\b"
        text = re.sub(pattern, kw, text, flags=re.IGNORECASE)

    text = re.sub(
        r"\b(PROPONE|DELIBERA)\s+D\s+I\s+(DELIBERARE|DICHIARARE)\b",
        r"\1 DI \2",
        text,
        flags=re.IGNORECASE,
    )

    return text


def guess_tool(path: Path) -> str:
    p = str(path).lower()
    if "docling" in p:
        return "docling"
    if "opendataloader" in p or "open_data_loader" in p or "odl" in p:
        return "opendataloader"
    if "marker" in p:
        return "marker"
    return "unknown"


def role_norm(label: str) -> str:
    label = (label or "").lower().replace("-", "_").replace(" ", "_")

    if label in {"section_header", "sectionheader", "heading", "title", "subtitle"}:
        return "heading"
    if label in {"text", "paragraph", "textinline", "line", "span"}:
        return "text"
    if label in {"list", "list_item", "listitem", "bullet", "bulleted_list"}:
        return "list_item"
    if label in {"table", "tablecell", "table_cell"}:
        return "table"
    if label in {"picture", "image", "figure"}:
        return "figure"
    if label in {"page_footer", "footer"}:
        return "footer"
    if label in {"page_header", "header"}:
        return "header"

    return "other"


def strip_initial_bullet_or_number(text: str) -> str:
    """Rimuove numerazioni e bullet all'inizio, senza toccare il contenuto."""
    text = text.strip()
    text = re.sub(r"^[-–—•●▪\*\s]+", "", text)
    text = re.sub(r"^\(?\d+\)?[\.)\-]\s+", "", text)
    text = re.sub(r"^[a-zA-Z][\.)]\s+", "", text)
    return text.strip()


def detect_marker(text: str) -> Optional[str]:
    """
    Riconosce il marker solo all'inizio o quasi-inizio del blocco.
    Questo evita falsi positivi dentro frasi lunghe.
    """
    t = normalize_text(text).lower()
    t = strip_initial_bullet_or_number(t)

    for marker, pattern in MARKERS.items():
        if re.match(pattern, t, flags=re.IGNORECASE):
            return marker

    return None


def detect_dispositive_stop(text: str, role: str = "") -> Optional[str]:
    """Riconosce l'inizio del dispositivo senza confonderlo con citazioni di delibere."""
    normalized = strip_initial_bullet_or_number(normalize_text(text)).strip()
    normalized = re.sub(r"^[*_#\s]+|[*_#\s]+$", "", normalized).strip()
    lowered = normalized.lower()

    proposal_patterns = (
        r"^(?:si\s+)?propone\s+d\s*i\s+deliberare\b",
        r"^propone\s+alla\s+g\.?\s*c\.?\s+d\s*i\s+deliberare\b",
    )
    if any(re.match(pattern, lowered) for pattern in proposal_patterns):
        return "PROPONE_DELIBERARE"

    # OpenDataLoader può fondere formula di voto e marker nello stesso
    # elemento: "Con votazione ... DELIBERA". In tale contesto DELIBERA è
    # dispositivo anche se non si trova all'inizio della stringa.
    inline_delibera = re.search(r"\bdelibera\b", lowered)
    if inline_delibera is not None:
        prefix = lowered[:inline_delibera.start()]
        if re.search(
            r"\b(?:con\s+vot(?:i|azione)|all['’]unanimit[aà]|"
            r"voti?\s+(?:favorevol|unanim)|votazione\s+(?:favorevol|unanim))",
            prefix,
        ):
            return "DELIBERA"

    # Formula autonoma o titolo: DELIBERA, DELIBERA CHE, DELIBERA QUANTO SEGUE.
    if re.match(r"^delibera(?:\s+che|\s+quanto\s+segue)?\s*[:;.-]?$", lowered):
        return "DELIBERA"

    # Dispositivo e primo comando possono essere estratti come una sola riga.
    if re.match(
        r"^delibera\s+d\s*i\s+(?!giunta\b|consiglio\b)\w+", lowered
    ):
        return "DELIBERA"

    # Formula con organo deliberante sulla stessa riga.
    if re.match(
        r"^(?:la\s+)?(?:giunta(?:\s+comunale)?|consiglio(?:\s+comunale)?)"
        r"\s+(?:comunale\s+)?delibera\b",
        lowered,
    ):
        return "DELIBERA"

    # Nei titoli Docling/Markdown accettiamo DELIBERA seguito da testo dispositivo,
    # ma non DELIBERA DI/DEL N., tipiche citazioni amministrative.
    if role == "heading" and re.match(r"^delibera\b", lowered):
        if not re.match(
            r"^delibera\s+(?:d\s*i|del|della|n\.?|nr\.?)\b", lowered
        ):
            return "DELIBERA"

    # Candidato dispositivo di attestazione. La presenza di un DELIBERA nello
    # stesso documento e le formule di certificazione vengono verificate in
    # segment_elements_to_blocks().
    attestation_prefix = lowered[:300]
    if (
        re.match(r"^(?:attestazione|attestato)\s+di\b", lowered)
        or re.search(r"\b(?:si\s+)?attesta\b", attestation_prefix)
    ):
        return "ATTESTA"

    return None


ATTESTATION_CERTIFICATE_PATTERN = re.compile(
    r"\b(?:"
    r"affiss[aoe]|pubblicat[aoe]|pubblicazione|albo\s+pretorio|"
    r"quindici\s+giorni|esecutiv(?:a|o|ita)|copia\s+conforme|"
    r"certificat[oa]\s+di|attestazione\s+di\s+pubblicazione|"
    r"attestato\s+di\s+esecutivita|copertura\s+finanziaria"
    r")\b",
    flags=re.IGNORECASE,
)


def is_attestation_certificate(text: str) -> bool:
    """Distingue le certificazioni accessorie dal dispositivo ATTESTA."""
    normalized = normalize_text(text)
    return bool(ATTESTATION_CERTIFICATE_PATTERN.search(normalized))


SECONDARY_DELIBERA_PATTERN = re.compile(
    r"(?P<transition>"
    r"(?:"
    r"\b(?:infine|successivamente|indi|quindi)\b|"
    r"\bcon\s+(?:"
    r"successiva\s+votazione|"
    r"voti\s+(?:unanimi|favorevoli)|"
    r"votazione\s+(?:unanime|favorevole)"
    r")\b"
    r").{0,1200}?"
    r"|"
    r"\b(?:la\s+giunta|il\s+consiglio)(?:\s+comunale)?\b"
    r".{0,600}?"
    r"|"
    r"\r?\n\s*(?=delibera\b)"
    r")"
    r"(?P<verb>\b(?:dichiara|delibera)\b)"
    r"(?=\s+(?:la\s+presente|d\s*i\s+dichiarare)\b)",
    flags=re.IGNORECASE | re.DOTALL,
)


NUMBERED_SECONDARY_DEVICE_PATTERN = re.compile(
    r"(?P<item>(?<!\w)(?:\(?\d+\)?|[a-z])[\.)]\s+)"
    r"(?="
    r"d\s*i\s+dichiarare\b.{0,500}?"
    r"\bimmediatamente\s+eseguibil[ei]\b.{0,300}?"
    r"\b(?:separat[ae]|successiv[ae])\b.{0,120}?\bvotazion"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)


POST_DEVICE_PROCEDURAL_PATTERN = re.compile(
    r"\b(?:con\s+successiva\s+votazione|successivamente)\b",
    flags=re.IGNORECASE,
)


POST_DEVICE_NARRATIVE_RESTART_PATTERN = re.compile(
    r"(?:^|(?<=[.;:]))\s*(?:vist[oaie]|richiamat[oaie])\b",
    flags=re.IGNORECASE,
)


POST_DELIBERA_MATTER_PATTERN = re.compile(
    r"\b(?:"
    r"(?:fatto\s*,?\s*)?"
    r"(?:(?:il\s+presente\s+(?:atto|verbale)\s+(?:viene\s+)?)?)"
    r"letto\s*,?\s*(?:approvato|confermato)(?:\s*,?\s*e)?\s+sottoscritto|"
    r"il\s+proponente\b|"
    r"(?:il\s+presidente|il\s+sindaco).{0,250}?"
    r"(?:il\s+segretario(?:\s+comunale)?)|"
    r"(?:certificat[oa]|referto|attestazione)\s+di\s+pubblicazione|"
    r"attestato\s+di\s+esecutivita|"
    r"allegato\s+alla\s+proposta|"
    r"visto\s*,?\s*si\s+esprime\s+parere|"
    r"firmato\s+da\s*:?.{0,180}?codice\s+fiscale|"
    r"(?:delibera(?:zione)?\s+(?:di|della)\s+giunta|"
    r"delibera\s+g\.?\s*c\.?).{0,140}?\bpag\.?\s*\d+|"
    r"comune\s+di\s+.{0,120}?[-\u2013]\s*deliberazione\s+n\.?\s*\d+"
    r".{0,100}?\b\d+\s*$"
    r")\b",
    flags=re.IGNORECASE | re.DOTALL,
)


POST_DELIBERA_ROW_PATTERN = re.compile(
    r"^(?:"
    r"(?:fatto\s*,?\s*)?"
    r"(?:(?:il\s+presente\s+(?:atto|verbale)\s+(?:viene\s+)?)?)"
    r"letto\s*,?\s*(?:approvato|confermato)(?:\s*,?\s*e)?\s+sottoscritto|"
    r"il\s+(?:proponente|presidente|sindaco|segretario(?:\s+comunale)?)\b|"
    r"(?:certificat[oa]|referto|attestazione)\s+di\s+pubblicazione|"
    r"attestato\s+di\s+esecutivita|"
    r"allegato\s+alla\s+proposta|"
    r"(?:si\s+)?attesta\b|"
    r"visto\s*,?\s*si\s+esprime\s+parere|"
    r"parere\s+(?:di|in\s+ordine\s+alla)\s+regolarita|"
    r"firmato\s+da\s*:|"
    r"(?:delibera(?:zione)?\s+(?:di|della)\s+giunta|"
    r"delibera\s+g\.?\s*c\.?).*?\bpag\.?\s*\d+|"
    r"comune\s+di\s+.*?[-\u2013]\s*deliberazione\s+n\.?\s*\d+"
    r")",
    flags=re.IGNORECASE,
)


def trim_post_delibera_matter(text: str) -> str:
    """Esclude firme, pareri e certificazioni collocati dopo il dispositivo."""
    matches = [
        match
        for match in (
            POST_DELIBERA_MATTER_PATTERN.search(text),
            POST_DEVICE_NARRATIVE_RESTART_PATTERN.search(text),
        )
        if match is not None
    ]
    if not matches:
        return text.strip()
    match = min(matches, key=lambda item: item.start())
    trimmed = text[:match.start()].rstrip()
    return trimmed or text.strip()


def split_delibera_devices(text: str) -> list[str]:
    """Chiude il dispositivo e separa eventuali decisioni autonome successive."""
    device_text = trim_post_delibera_matter(text)
    boundaries = [
        (match.start("transition"), match.start("verb"))
        for match in SECONDARY_DELIBERA_PATTERN.finditer(device_text)
    ]
    boundaries.extend(
        (match.start("item"), match.start("item"))
        for match in NUMBERED_SECONDARY_DEVICE_PATTERN.finditer(device_text)
    )
    boundaries.sort()
    if not boundaries:
        procedural_tail = POST_DEVICE_PROCEDURAL_PATTERN.search(device_text)
        if procedural_tail is not None:
            device_text = device_text[:procedural_tail.start()].rstrip()
        return [device_text]

    devices = [device_text[:boundaries[0][0]].strip()]
    for index, (_, device_start) in enumerate(boundaries):
        end = (
            boundaries[index + 1][0]
            if index + 1 < len(boundaries)
            else len(device_text)
        )
        devices.append(device_text[device_start:end].strip())

    if any(not device for device in devices):
        return [device_text]
    return devices


def collect_terminal_device_text(
    group_rows: list[tuple[Any, pd.Series]],
    start_position: int,
    device_marker: str,
    stop_before_position: Optional[int] = None,
) -> tuple[str, Any, int, int]:
    """Raccoglie un dispositivo terminale fino alle formule di chiusura."""
    first_row = group_rows[start_position][1]
    first_text = str(first_row.get("text", "")).strip()
    marker_match = re.search(
        rf"\b{re.escape(device_marker)}\b", first_text, flags=re.IGNORECASE
    )
    first_device_text = (
        first_text[marker_match.start():].strip()
        if marker_match is not None
        else first_text
    )

    parts = [first_device_text] if first_device_text else []
    page_end = first_row.get("page")
    element_count = 1 if first_device_text else 0
    last_consumed_position = start_position

    if trim_post_delibera_matter(first_device_text) != first_device_text:
        return (
            trim_post_delibera_matter(first_device_text),
            page_end,
            element_count,
            last_consumed_position,
        )

    for following_position, (_, following_row) in enumerate(
        group_rows[start_position + 1:], start=start_position + 1
    ):
        if (
            stop_before_position is not None
            and following_position >= stop_before_position
        ):
            break

        following_text = str(following_row.get("text", "")).strip()
        if not following_text:
            continue

        normalized_following = normalize_text(following_text)
        if (
            POST_DELIBERA_ROW_PATTERN.match(normalized_following)
            or POST_DEVICE_NARRATIVE_RESTART_PATTERN.match(normalized_following)
        ):
            break
        if (
            following_row.get("role_norm") == "table"
            or following_row.get("label_raw") == "table"
        ):
            break
        if is_institutional_heading(
            following_text, str(following_row.get("role_norm", ""))
        ):
            continue
        if bool(following_row.get("is_noise", False)):
            continue

        boundaries = [
            match
            for match in (
                POST_DELIBERA_MATTER_PATTERN.search(following_text),
                POST_DEVICE_NARRATIVE_RESTART_PATTERN.search(following_text),
            )
            if match is not None
        ]
        boundary = (
            min(boundaries, key=lambda item: item.start())
            if boundaries
            else None
        )
        text_before_boundary = (
            following_text[:boundary.start()].strip()
            if boundary is not None
            else following_text
        )
        if text_before_boundary:
            parts.append(text_before_boundary)
            element_count += 1
            last_consumed_position = following_position
            if pd.notna(following_row.get("page")):
                page_end = following_row.get("page")
        if boundary is not None:
            break

    return (
        "\n".join(parts).strip(),
        page_end,
        element_count,
        last_consumed_position,
    )


PROCEDURAL_STOP_PATTERN = re.compile(
    r"(?:^|(?<=[;:.]))\s*(?:"
    r"a\s+votazione\s+(?:unanime|favorevole)|"
    r"con\s+votazione\s+(?:unanime|favorevole)|"
    r"con\s+voti\s+(?:unanimi|favorevoli)|"
    r"si\s+procede\s+a\s+votazione|"
    r"procedutosi\s+a\s+votazione|"
    r"udita\s+(?:quindi\s+)?la\s+proposta.*?si\s+procede\s+a\s+votazione|"
    r"a\s+seguito\s+di\s+votazione|"
    r"all['’]esito\s+della\s+votazione"
    r")\b",
    flags=re.IGNORECASE,
)


PROPOSAL_STOP_PATTERN = re.compile(
    r"(?:^|(?<=[;:.]))\s*(?:si\s+)?propone(?:\s+di\s+(?:deliberare|approvare))?\b",
    flags=re.IGNORECASE,
)


def procedural_stop_position(text: str) -> Optional[int]:
    """Restituisce dove inizia una formula di votazione che chiude la narrativa."""
    match = PROCEDURAL_STOP_PATTERN.search(normalize_text(text))
    return match.start() if match else None


def proposal_stop_position(text: str) -> Optional[int]:
    """Trova una proposta dispositiva accodata all'ultimo blocco narrativo."""
    match = PROPOSAL_STOP_PATTERN.search(normalize_text(text))
    return match.start() if match else None


def is_institutional_heading(text: str, role: str = "") -> bool:
    """Individua intestazioni che possono riaprire la narrativa dopo una proposta."""
    normalized = normalize_text(text).strip(" :;.-").lower()
    pattern = (
        r"^(?:la\s+)?giunta(?:\s+comunale)?|"
        r"^(?:il\s+)?consiglio(?:\s+comunale)?|"
        r"^(?:il\s+)?commissario(?:\s+straordinario)?"
    )
    return role == "heading" and re.fullmatch(pattern, normalized) is not None


def is_noise(row: pd.Series) -> bool:
    text = normalize_text(row.get("text", "")).lower()
    role = row.get("role_norm", "")

    if role in {"table", "figure", "footer"}:
        return True

    if len(text) == 0:
        return True

    for pat in NOISE_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True

    return False


# ============================================================
# 4. LETTURA MARKDOWN
# ============================================================

def read_pdf(path: Path) -> pd.DataFrame:
    """Estrae il testo di un PDF, suddividendolo per pagina e per riga."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        raise RuntimeError(
            "Impossibile leggere i PDF: il programma 'pdftotext' non è disponibile."
        )

    result = subprocess.run(
        [pdftotext, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        check=True,
    )
    extracted = result.stdout.decode("utf-8", errors="ignore")
    rows = []
    order = 0

    for page_number, page_text in enumerate(extracted.split("\f"), start=1):
        for line in page_text.splitlines():
            text = normalize_text(line)
            if not text:
                continue

            order += 1
            rows.append({
                "id_atto": path.stem,
                "tool": "pdftotext",
                "source_format": "pdf",
                "source_file": path.name,
                "order": order,
                "page": page_number,
                "label_raw": "text",
                "role_norm": "text",
                "text": text,
                "bbox": None,
            })

    return pd.DataFrame(rows)


def classify_markdown_line(line: str) -> str:
    line = line.strip()

    if re.match(r"^(?:\*\*|__).+(?:\*\*|__)$", line):
        return "heading"
    if line.startswith("!["):
        return "image"
    if re.match(r"^#{1,6}\s+", line):
        return "heading"
    if line.startswith("|"):
        return "table"
    if re.match(r"^\s*[-*+]\s+", line):
        return "list_item"
    if re.match(r"^\s*[•●▪]\s*", line):
        return "list_item"

    return "paragraph"


def clean_markdown_line(line: str) -> str:
    line = re.sub(r"^#{1,6}\s+", "", line)
    line = re.sub(r"^\s*[-*+]\s+", "", line)
    line = re.sub(r"^\s*[•●▪]\s*", "", line)
    line = line.replace("**", "")
    return normalize_text(line)


def read_markdown(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    rows = []

    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue

        label = classify_markdown_line(line)
        text = clean_markdown_line(line)

        rows.append({
            "id_atto": path.stem,
            "tool": guess_tool(path),
            "source_format": "markdown",
            "source_file": path.name,
            "order": i,
            "page": None,
            "label_raw": label,
            "role_norm": role_norm(label),
            "text": text,
            "bbox": None,
        })

    return pd.DataFrame(rows)


# ============================================================
# 5. LETTURA JSON DOCLING
# ============================================================

def resolve_ref(doc: Dict[str, Any], ref: str) -> Any:
    """Risolve riferimenti tipo #/texts/0, #/groups/1, #/tables/0."""
    ref = ref.replace("#/", "")
    parts = ref.split("/")

    obj: Any = doc
    for part in parts:
        if part.isdigit():
            obj = obj[int(part)]
        else:
            obj = obj[part]

    return obj


def first_prov(obj: Dict[str, Any]) -> Dict[str, Any]:
    prov = obj.get("prov")
    if isinstance(prov, list) and len(prov) > 0:
        return prov[0]
    return {}


def extract_docling_object(
    doc: Dict[str, Any],
    ref: str,
    rows: List[Dict[str, Any]],
    meta: Dict[str, Any],
    order_counter: List[int],
) -> None:
    obj = resolve_ref(doc, ref)

    if ref.startswith("#/groups/"):
        for child in obj.get("children", []):
            child_ref = child.get("$ref")
            if child_ref:
                extract_docling_object(doc, child_ref, rows, meta, order_counter)
        return

    order_counter[0] += 1
    prov = first_prov(obj)

    if ref.startswith("#/texts/"):
        label = obj.get("label", "text")
        text = obj.get("text", "")
    elif ref.startswith("#/tables/"):
        label = "table"
        text = "[table]"
    elif ref.startswith("#/pictures/"):
        label = "picture"
        text = "[figure]"
    else:
        label = obj.get("label", "other")
        text = obj.get("text", "")

    rows.append({
        "id_atto": meta["id_atto"],
        "tool": meta["tool"],
        "source_format": "json",
        "source_file": meta["source_file"],
        "order": order_counter[0],
        "page": prov.get("page_no"),
        "label_raw": label,
        "role_norm": role_norm(label),
        "text": normalize_text(text),
        "bbox": json.dumps(prov.get("bbox"), ensure_ascii=False) if prov.get("bbox") else None,
    })


def read_docling_json(path: Path, doc: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    meta = {
        "id_atto": path.stem,
        "tool": guess_tool(path),
        "source_file": path.name,
    }

    order_counter = [0]
    body_children = doc.get("body", {}).get("children", [])

    for child in body_children:
        ref = child.get("$ref")
        if ref:
            extract_docling_object(doc, ref, rows, meta, order_counter)

    return pd.DataFrame(rows)


def extract_with_docling(pdf_paths: List[Path], extraction_dir: Path) -> List[Dict[str, str]]:
    """Estrae i PDF unici con Docling e salva un JSON tecnico per ciascun atto."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            "Docling non è installato nell'interprete Python in uso. "
            "Installa il pacchetto 'docling' oppure usa l'interprete corretto."
        ) from exc

    extraction_dir.mkdir(parents=True, exist_ok=True)
    worker_path = SCRIPT_DIR / "_docling_extract_worker.py"
    pdfs = sorted(pdf_paths, key=natural_sort_key)
    errors: List[Dict[str, str]] = []

    for index, pdf_path in enumerate(pdfs, start=1):
        json_path = extraction_dir / f"{pdf_path.stem}.json"
        markdown_path = extraction_dir / f"{pdf_path.stem}.md"
        if json_path.exists() and markdown_path.exists():
            print(f"[DOCLING {index}/{len(pdfs)}] già estratto: {pdf_path.name}")
            continue
        print(f"[DOCLING {index}/{len(pdfs)}] {pdf_path.name}")
        try:
            process = subprocess.run(
                [sys.executable, str(worker_path), str(pdf_path), str(extraction_dir)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=1800,
            )
            if process.returncode != 0:
                message = (process.stderr or process.stdout or "errore nativo Docling").strip()
                raise RuntimeError(message[-2000:])
        except Exception as exc:
            errors.append({"file": pdf_path.name, "errore": str(exc)})
            print(f"[ERRORE DOCLING] {pdf_path.name}: {exc}")

    return errors


def extract_with_local_rapidocr(pdf_path: Path, extraction_dir: Path) -> None:
    """Fallback autonomo OCR: produce JSON/Markdown compatibili con il reader."""
    import fitz
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    document = {
        "file name": pdf_path.name,
        "number of pages": 0,
        "kids": [],
    }
    markdown_pages: List[str] = []
    item_id = 0
    with fitz.open(pdf_path) as pdf:
        document["number of pages"] = len(pdf)
        for page_number, page in enumerate(pdf, start=1):
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB, alpha=False
            )
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, 3
            )
            result, _ = engine(image)
            page_lines: List[str] = []
            for detection in result or []:
                box, text = detection[0], str(detection[1]).strip()
                if not text:
                    continue
                item_id += 1
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                document["kids"].append({
                    "type": "paragraph",
                    "id": item_id,
                    "page number": page_number,
                    "bounding box": [min(xs), min(ys), max(xs), max(ys)],
                    "content": text,
                })
                page_lines.append(text)
            markdown_pages.append("\n\n".join(page_lines))

    extraction_dir.mkdir(parents=True, exist_ok=True)
    (extraction_dir / f"{pdf_path.stem}.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (extraction_dir / f"{pdf_path.stem}.md").write_text(
        "\n\n---\n\n".join(markdown_pages), encoding="utf-8"
    )


def extract_with_opendataloader(pdf_paths: List[Path], extraction_dir: Path) -> List[Dict[str, str]]:
    """Estrae in batch i PDF con OpenDataLoader nei formati JSON e Markdown."""
    try:
        import opendataloader_pdf
    except ImportError as exc:
        raise RuntimeError(
            "OpenDataLoader non è installato. Installa 'opendataloader-pdf' "
            "e verifica che Java 11 o successivo sia disponibile nel PATH."
        ) from exc

    extraction_dir.mkdir(parents=True, exist_ok=True)
    opendataloader_pdf.convert(
        input_path=[str(path) for path in sorted(pdf_paths, key=natural_sort_key)],
        output_dir=str(extraction_dir),
        format="json,markdown",
        quiet=True,
    )

    suspicious_pdfs = []
    by_stem = {path.stem: path for path in pdf_paths}
    for json_path in extraction_dir.glob("*.json"):
        try:
            document = json.loads(json_path.read_text(encoding="utf-8"))
            page_parts: Dict[Any, List[str]] = {}
            page_has_image: Dict[Any, bool] = {}
            for kid in document.get("kids", []):
                page = kid.get("page number")
                nested_items = kid.get("list items") or []
                nested_text = " ".join(
                    str(item.get("content") or item.get("text") or "")
                    for item in nested_items
                    if isinstance(item, dict)
                )
                content = " ".join(filter(None, [
                    str(kid.get("content") or kid.get("text") or ""),
                    nested_text,
                ]))
                page_parts.setdefault(page, []).append(content)
                if str(kid.get("type", "")).lower() in {"image", "picture", "figure"}:
                    page_has_image[page] = True

            needs_ocr = False
            declared_page_count = int(document.get("number of pages") or 0)
            declared_pages = set(range(1, declared_page_count + 1))
            for page in declared_pages | set(page_parts):
                parts = page_parts.get(page, [])
                combined = " ".join(parts)
                letters = len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", combined))
                digits = len(re.findall(r"\d", combined))
                if letters == 0 or (
                    letters < 20 and (digits >= 2 or page_has_image.get(page, False))
                ):
                    needs_ocr = True
                    break

            if needs_ocr and json_path.stem in by_stem:
                suspicious_pdfs.append(by_stem[json_path.stem])
        except Exception:
            continue

    if not suspicious_pdfs:
        return []

    print(
        "OpenDataLoader: OCR RapidOCR necessario per: "
        + ", ".join(path.name for path in suspicious_pdfs)
    )
    hybrid_executable = shutil.which("opendataloader-pdf-hybrid")
    if hybrid_executable is None:
        return [{
            "file": path.name,
            "errore": "Backend ibrido OpenDataLoader non disponibile per RapidOCR",
        } for path in suspicious_pdfs]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    log_path = extraction_dir / "rapidocr_hybrid.log"
    errors: List[Dict[str, str]] = []
    with log_path.open("w", encoding="utf-8") as log_file:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        server = subprocess.Popen(
            [
                hybrid_executable,
                "--host", "127.0.0.1",
                "--port", str(port),
                "--force-ocr",
                "--ocr-engine", "rapidocr",
                "--device", "cpu",
                "--log-level", "warning",
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        try:
            deadline = time.time() + 180
            while time.time() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Il backend RapidOCR si è arrestato durante l'avvio")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(1)
            else:
                raise TimeoutError("Timeout durante l'avvio del backend RapidOCR")

            for pdf_path in suspicious_pdfs:
                try:
                    # OpenDataLoader non sovrascrive sempre gli output già
                    # presenti: eliminiamo soltanto quelli dell'atto sospetto
                    # prima del secondo passaggio OCR.
                    for old_output in (
                        extraction_dir / f"{pdf_path.stem}.json",
                        extraction_dir / f"{pdf_path.stem}.md",
                    ):
                        old_output.unlink(missing_ok=True)
                    image_dir = extraction_dir / f"{pdf_path.stem}_images"
                    if image_dir.exists():
                        shutil.rmtree(image_dir)
                    opendataloader_pdf.convert(
                        input_path=[str(pdf_path)],
                        output_dir=str(extraction_dir),
                        format="json,markdown",
                        quiet=True,
                        hybrid="docling-fast",
                        hybrid_mode="full",
                        hybrid_url=f"http://127.0.0.1:{port}",
                        hybrid_timeout="1800",
                    )
                except Exception as exc:
                    try:
                        extract_with_local_rapidocr(pdf_path, extraction_dir)
                    except Exception as fallback_exc:
                        errors.append({
                            "file": pdf_path.name,
                            "errore": f"{exc}; fallback RapidOCR: {fallback_exc}",
                        })
        except Exception as exc:
            for path in suspicious_pdfs:
                try:
                    extract_with_local_rapidocr(path, extraction_dir)
                except Exception as fallback_exc:
                    errors.append({
                        "file": path.name,
                        "errore": f"{exc}; fallback RapidOCR: {fallback_exc}",
                    })
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()

    return errors


def find_marker_output_file(output_root: Path, stem: str, suffixes: set[str]) -> Optional[Path]:
    candidates = [
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes
    ]
    if not candidates:
        return None

    non_metadata = [
        path
        for path in candidates
        if "metadata" not in path.name.lower() and "meta" not in path.name.lower()
    ]
    candidates = non_metadata or candidates
    preferred = [path for path in candidates if path.stem.lower() == stem.lower()]
    candidates = preferred or candidates
    return max(candidates, key=lambda path: path.stat().st_size)


def run_marker_single(
    marker_executable: str,
    pdf_path: Path,
    extraction_dir: Path,
    output_format: str,
    disable_ocr: bool = False,
) -> None:
    suffixes = {"markdown": {".md", ".markdown"}, "json": {".json"}}[output_format]
    destination = extraction_dir / f"{pdf_path.stem}{'.md' if output_format == 'markdown' else '.json'}"
    if destination.exists():
        return

    temp_dir = extraction_dir / f"__marker_tmp_{pdf_path.stem}_{output_format}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    command = [
        marker_executable,
        str(pdf_path),
        "--output_format",
        output_format,
        "--output_dir",
        str(temp_dir),
        "--mode",
        "fast",
        "--disable_image_extraction",
    ]
    if disable_ocr:
        command.append("--disable_ocr")
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=1800,
        )
        if process.returncode != 0:
            message = (process.stderr or process.stdout or "errore Marker").strip()
            raise RuntimeError(message[-2000:])

        produced = find_marker_output_file(temp_dir, pdf_path.stem, suffixes)
        if produced is None:
            raise RuntimeError(f"Marker non ha prodotto output {output_format}")
        shutil.copy2(produced, destination)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def extract_with_marker(
    pdf_paths: List[Path], extraction_dir: Path, disable_ocr: bool = False
) -> List[Dict[str, str]]:
    """Estrae i PDF con Marker, producendo Markdown e JSON locali."""
    marker_executable = shutil.which("marker_single")
    if marker_executable is None:
        raise RuntimeError(
            "Marker non e' installato o non e' nel PATH. "
            "Installa 'marker-pdf' e verifica che il comando 'marker_single' sia disponibile."
        )

    extraction_dir.mkdir(parents=True, exist_ok=True)
    errors: List[Dict[str, str]] = []
    pdfs = sorted(pdf_paths, key=natural_sort_key)
    for index, pdf_path in enumerate(pdfs, start=1):
        if (extraction_dir / f"{pdf_path.stem}.md").exists() and (
            extraction_dir / f"{pdf_path.stem}.json"
        ).exists():
            print(f"[MARKER {index}/{len(pdfs)}] gia' estratto: {pdf_path.name}")
            continue

        print(f"[MARKER {index}/{len(pdfs)}] {pdf_path.name}")
        for output_format in ("markdown", "json"):
            try:
                run_marker_single(
                    marker_executable,
                    pdf_path,
                    extraction_dir,
                    output_format,
                    disable_ocr=disable_ocr,
                )
            except Exception as exc:
                errors.append({
                    "file": pdf_path.name,
                    "formato": output_format,
                    "errore": str(exc),
                })
                print(f"[ERRORE MARKER {output_format.upper()}] {pdf_path.name}: {exc}")
    return errors


# ============================================================
# 6. LETTURA JSON OPENDATALOADER / JSON SEMPLIFICATO
# ============================================================

def read_opendataloader_json(path: Path, doc: Dict[str, Any]) -> pd.DataFrame:
    kids = doc.get("kids", [])
    rows = []

    for i, kid in enumerate(kids, start=1):
        label = kid.get("type", "other")

        if label == "table":
            nr = kid.get("number of rows")
            nc = kid.get("number of columns")
            text = f"[table {nr}x{nc}]"
        else:
            text = kid.get("content") or kid.get("text") or ""

        rows.append({
            "id_atto": path.stem,
            "tool": guess_tool(path),
            "source_format": "json",
            "source_file": path.name,
            "order": i,
            "page": kid.get("page number"),
            "label_raw": label,
            "role_norm": role_norm(label),
            "text": normalize_text(text),
            "bbox": json.dumps(kid.get("bounding box"), ensure_ascii=False) if kid.get("bounding box") else None,
        })

    return pd.DataFrame(rows)


def clean_marker_json_text(value: Any) -> str:
    if isinstance(value, (dict, list)) or value is None:
        return ""
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_text(text)


def marker_label(obj: Dict[str, Any]) -> str:
    for key in ("block_type", "type", "label", "category", "role"):
        value = obj.get(key)
        if value:
            return str(value)
    return "paragraph"


def marker_page(obj: Dict[str, Any]) -> Any:
    for key in ("page", "page_id", "page_number", "page_idx"):
        if key in obj:
            return obj.get(key)
    return None


def marker_bbox(obj: Dict[str, Any]) -> Any:
    for key in ("bbox", "bounding_box", "box", "polygon", "position"):
        value = obj.get(key)
        if value:
            return value
    return None


def marker_text(obj: Dict[str, Any]) -> str:
    for key in ("text", "content", "html", "markdown", "block_text", "block_html"):
        text = clean_marker_json_text(obj.get(key))
        if text:
            return text
    return ""


def marker_children(obj: Dict[str, Any]) -> list[Any]:
    children: list[Any] = []
    for key in ("children", "blocks", "items", "lines", "spans"):
        value = obj.get(key)
        if isinstance(value, list):
            children.extend(value)
    return children


def read_marker_json(path: Path, doc: Any) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    order = 0

    def visit(node: Any, inherited_page: Any = None) -> None:
        nonlocal order
        if isinstance(node, list):
            for item in node:
                visit(item, inherited_page)
            return
        if not isinstance(node, dict):
            return

        page = marker_page(node)
        if page is None:
            page = inherited_page
        text = marker_text(node)
        children = marker_children(node)

        if text and not children:
            order += 1
            label = marker_label(node)
            bbox = marker_bbox(node)
            rows.append({
                "id_atto": path.stem,
                "tool": guess_tool(path),
                "source_format": "json",
                "source_file": path.name,
                "order": order,
                "page": page,
                "label_raw": label,
                "role_norm": role_norm(label),
                "text": text,
                "bbox": json.dumps(bbox, ensure_ascii=False) if bbox else None,
            })
            return

        for child in children:
            visit(child, page)

        if text and not any(isinstance(child, dict) and marker_text(child) for child in children):
            order += 1
            label = marker_label(node)
            bbox = marker_bbox(node)
            rows.append({
                "id_atto": path.stem,
                "tool": guess_tool(path),
                "source_format": "json",
                "source_file": path.name,
                "order": order,
                "page": page,
                "label_raw": label,
                "role_norm": role_norm(label),
                "text": text,
                "bbox": json.dumps(bbox, ensure_ascii=False) if bbox else None,
            })

    if isinstance(doc, dict):
        for key in ("children", "blocks", "pages"):
            if isinstance(doc.get(key), list):
                visit(doc[key])
                break
        else:
            visit(doc)
    else:
        visit(doc)

    return pd.DataFrame(rows)


def read_json_any(path: Path) -> pd.DataFrame:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        doc = json.load(f)

    if guess_tool(path) == "marker":
        return read_marker_json(path, doc)

    if doc.get("schema_name") == "DoclingDocument":
        return read_docling_json(path, doc)

    if "kids" in doc:
        return read_opendataloader_json(path, doc)

    print(f"[ATTENZIONE] Schema JSON non riconosciuto: {path}")
    return pd.DataFrame()


# ============================================================
# 7. LETTURA DI TUTTI GLI OUTPUT
# ============================================================

def read_all_outputs(input_root: Path, selected_format: str = SELECTED_FORMAT) -> pd.DataFrame:
    suffixes = {
        "json": ["*.json"],
        "markdown": ["*.md", "*.markdown"],
    }
    if selected_format not in suffixes:
        raise ValueError(f"Formato di segmentazione non supportato: {selected_format}")
    files = sorted(
        path
        for pattern in suffixes[selected_format]
        for path in input_root.rglob(pattern)
    )

    all_dfs = []

    for path in files:
        try:
            if path.suffix.lower() == ".pdf":
                df = read_pdf(path)
            elif path.suffix.lower() == ".json":
                df = read_json_any(path)
            elif path.suffix.lower() == ".md":
                df = read_markdown(path)
            else:
                continue

            if not df.empty:
                all_dfs.append(df)

        except Exception as e:
            print(f"[ERRORE] {path}: {e}")

    if not all_dfs:
        return pd.DataFrame()

    return pd.concat(all_dfs, ignore_index=True)


# ============================================================
# 8. COSTRUZIONE BLOCCHI NARRATIVI
# ============================================================

def build_blocks(elements: pd.DataFrame) -> pd.DataFrame:
    """
    Crea blocchi narrativi.
    Ogni nuovo marker apre un nuovo blocco.
    Gli elementi successivi senza marker vengono accodati al blocco corrente.

    DELIBERA chiude normalmente la narrativa. Se nello stesso atto è presente
    un PROPONE terminale successivo, conserva anche la narrativa della proposta
    allegata e chiude definitivamente dopo il relativo dispositivo.
    """
    rows = []

    required_cols = {"id_atto", "tool", "source_format", "order", "text", "marker_detected", "is_noise"}
    missing = required_cols.difference(elements.columns)
    if missing:
        raise ValueError(f"Colonne mancanti in elements: {missing}")

    for (id_atto, tool, source_format), group in elements.groupby(
        ["id_atto", "tool", "source_format"], dropna=False
    ):
        group = group.sort_values("order").copy()

        current_block: Optional[Dict[str, Any]] = None
        proposal_block: Optional[Dict[str, Any]] = None
        block_order = 0
        marker_counts: Dict[str, int] = {}
        skipping_proposal_device = False
        awaiting_deliberation = False
        has_deliberation_terminal = False
        has_explicit_delibera = (
            "stop_detected" in group.columns
            and group["stop_detected"].eq("DELIBERA").any()
        )
        group_rows = list(group.iterrows())
        proposal_device_positions = [
            position
            for position, (_, candidate_row) in enumerate(group_rows)
            if (
                pd.notna(candidate_row.get("proposal_stop_pos"))
                or candidate_row.get("stop_detected") == "PROPONE_DELIBERARE"
            )
        ]
        skip_through_position = -1
        continue_to_followup_proposal = False

        for row_position, (_, row) in enumerate(group_rows):
            if row_position <= skip_through_position:
                continue

            confirmed_waiting_terminal = False
            institutional_heading = is_institutional_heading(
                row.get("text", ""), str(row.get("role_norm", ""))
            )

            # La formula di votazione chiude la narrativa, ma non l'atto:
            # continuiamo a cercare il DELIBERA o l'ATTESTA terminale senza
            # creare altri blocchi narrativi nel tratto intermedio.
            if awaiting_deliberation:
                waiting_stop = row.get("stop_detected")
                if pd.isna(waiting_stop):
                    waiting_stop = None
                if waiting_stop not in {"DELIBERA", "ATTESTA"}:
                    continue
                awaiting_deliberation = False
                confirmed_waiting_terminal = True

            # Dopo PROPONE raccogliamo il dispositivo in un blocco non
            # narrativo e riprendiamo dal successivo marker narrativo.
            if skipping_proposal_device:
                next_marker = row.get("marker_detected")
                next_stop = row.get("stop_detected")
                if pd.isna(next_marker):
                    next_marker = None
                if pd.isna(next_stop):
                    next_stop = None
                if next_stop == "DELIBERA":
                    if proposal_block is not None:
                        rows.append(proposal_block)
                        proposal_block = None
                    skipping_proposal_device = False
                if next_marker not in {None, "DELIBERA", "PROPONE_DELIBERARE"}:
                    if proposal_block is not None:
                        rows.append(proposal_block)
                        proposal_block = None
                    skipping_proposal_device = False
                elif skipping_proposal_device:
                    is_table = row.get("role_norm") == "table" or row.get("label_raw") == "table"
                    if (
                        proposal_block is not None
                        and not institutional_heading
                        and not is_table
                        and not bool(row["is_noise"])
                    ):
                        proposal_text = str(row.get("text", "")).strip()
                        if proposal_text:
                            proposal_block["testo_blocco"] += " " + proposal_text
                            if pd.notna(row.get("page")):
                                proposal_block["page_end"] = row.get("page")
                            proposal_block["n_elementi"] += 1
                    continue

            # L'intestazione dell'organo non è testo narrativo, ma può essere
            # ripetuta a cambio pagina nel mezzo di un blocco: la ignoriamo
            # senza chiudere il blocco corrente.
            if institutional_heading:
                continue

            # Le tabelle delimitano il blocco corrente ma non l'intero atto:
            # dopo presenze, pareri o prospetti la narrativa può continuare.
            if row.get("role_norm") == "table" or row.get("label_raw") == "table":
                if current_block is not None:
                    rows.append(current_block)
                    current_block = None
                continue

            if bool(row["is_noise"]) and not confirmed_waiting_terminal:
                continue

            original_text = row["text"]
            text = original_text
            marker = row["marker_detected"]
            stop_marker = row.get("stop_detected")
            procedural_pos = row.get("procedural_stop_pos")
            proposal_pos = row.get("proposal_stop_pos")
            if pd.isna(marker):
                marker = None
            if pd.isna(stop_marker):
                stop_marker = None
            if pd.isna(procedural_pos):
                procedural_pos = None
            if pd.isna(proposal_pos):
                proposal_pos = None

            # Se la formula di votazione è accodata all'ultimo elemento,
            # conserviamo solo il testo narrativo che la precede.
            stop_after_row = procedural_pos is not None
            if stop_after_row:
                text = text[:int(procedural_pos)].rstrip(" ;:,.\t")

            # Se PROPONE è accodato a una frase narrativa, conserviamo il
            # prefisso nel blocco corrente e il resto nel dispositivo separato.
            proposal_after_row = proposal_pos is not None
            if proposal_after_row:
                text = text[:int(proposal_pos)].rstrip(" ;:,.\t")

            # Un marker lessicale non basta per fermare il documento: ad esempio
            # "DELIBERA Numero 62" e "DELIBERA DELLA GIUNTA MUNICIPALE" sono
            # intestazioni, non l'inizio del dispositivo. Lo stop deve essere
            # confermato da detect_dispositive_stop().
            effective_stop = stop_marker if stop_marker in STOP_MARKERS else None
            attestation_certificate = (
                effective_stop == "ATTESTA"
                and is_attestation_certificate(original_text)
            )
            if (
                effective_stop == "ATTESTA"
                and (
                    has_explicit_delibera
                    or attestation_certificate
                )
            ):
                effective_stop = None

            if attestation_certificate:
                if current_block is not None:
                    rows.append(current_block)
                    current_block = None
                continue

            # Il dispositivo della proposta viene conservato separatamente
            # fino al successivo marker narrativo.
            if effective_stop == "PROPONE_DELIBERARE":
                if current_block is not None:
                    rows.append(current_block)
                    current_block = None
                if continue_to_followup_proposal:
                    (
                        proposal_text,
                        proposal_page_end,
                        proposal_elements,
                        _,
                    ) = collect_terminal_device_text(
                        group_rows, row_position, "PROPONE"
                    )
                    block_order += 1
                    marker_counts["DISPOSITIVO_PROPOSTA"] = (
                        marker_counts.get("DISPOSITIVO_PROPOSTA", 0) + 1
                    )
                    rows.append({
                        "id_atto": id_atto,
                        "tool": tool,
                        "source_format": source_format,
                        "ordine_blocco": block_order,
                        "tipo_blocco": "DISPOSITIVO_PROPOSTA",
                        "tipo_blocco_progressivo": (
                            "DISPOSITIVO_PROPOSTA_"
                            f"{marker_counts['DISPOSITIVO_PROPOSTA']}"
                        ),
                        "macro_sezione": "DISPOSITIVO_PROPOSTA",
                        "is_narrativa": 0,
                        "testo_blocco": proposal_text,
                        "page_start": row.get("page"),
                        "page_end": proposal_page_end,
                        "bbox_start": row.get("bbox"),
                        "n_elementi": proposal_elements,
                    })
                    continue_to_followup_proposal = False
                    break
                block_order += 1
                marker_counts["DISPOSITIVO_PROPOSTA"] = marker_counts.get("DISPOSITIVO_PROPOSTA", 0) + 1
                proposal_block = {
                    "id_atto": id_atto,
                    "tool": tool,
                    "source_format": source_format,
                    "ordine_blocco": block_order,
                    "tipo_blocco": "DISPOSITIVO_PROPOSTA",
                    "tipo_blocco_progressivo": f"DISPOSITIVO_PROPOSTA_{marker_counts['DISPOSITIVO_PROPOSTA']}",
                    "macro_sezione": "DISPOSITIVO_PROPOSTA",
                    "is_narrativa": 0,
                    "testo_blocco": original_text,
                    "page_start": row.get("page"),
                    "page_end": row.get("page"),
                    "bbox_start": row.get("bbox"),
                    "n_elementi": 1,
                }
                skipping_proposal_device = True
                continue

            # DELIBERA è invece il dispositivo finale dell'organo.
            if effective_stop == "DELIBERA":
                if current_block is not None:
                    rows.append(current_block)
                    current_block = None
                later_proposal_position = next(
                    (
                        position
                        for position in proposal_device_positions
                        if position > row_position
                    ),
                    None,
                )
                followup_start_position = later_proposal_position
                if later_proposal_position is not None:
                    proposal_heading_position = next(
                        (
                            position
                            for position in range(
                                row_position + 1, later_proposal_position
                            )
                            if re.match(
                                r"^(?:proposta\s+di\s+deliberazione|"
                                r"il\s+proponente)\b",
                                normalize_text(
                                    group_rows[position][1].get("text", "")
                                ).lower(),
                            )
                        ),
                        None,
                    )
                    if proposal_heading_position is not None:
                        followup_start_position = proposal_heading_position
                        heading_page = group_rows[
                            proposal_heading_position
                        ][1].get("page")
                        delibera_page = row.get("page")
                        if (
                            pd.notna(heading_page)
                            and pd.notna(delibera_page)
                            and heading_page != delibera_page
                        ):
                            followup_start_position = next(
                                (
                                    position
                                    for position in range(
                                        row_position + 1,
                                        proposal_heading_position + 1,
                                    )
                                    if group_rows[position][1].get("page")
                                    == heading_page
                                ),
                                proposal_heading_position,
                            )
                    else:
                        followup_start_position = next(
                            (
                                position
                                for position in range(
                                    row_position + 1, later_proposal_position
                                )
                                if (
                                    pd.notna(
                                        group_rows[position][1].get(
                                            "marker_detected"
                                        )
                                    )
                                    and group_rows[position][1].get(
                                        "marker_detected"
                                    )
                                    not in {
                                        "DELIBERA",
                                        "PROPONE_DELIBERARE",
                                    }
                                )
                            ),
                            later_proposal_position,
                        )
                (
                    terminal_text,
                    terminal_page_end,
                    terminal_elements,
                    terminal_end_position,
                ) = collect_terminal_device_text(
                    group_rows,
                    row_position,
                    "DELIBERA",
                    stop_before_position=followup_start_position,
                )
                device_texts = split_delibera_devices(terminal_text)
                for device_index, device_text in enumerate(device_texts, start=1):
                    block_order += 1
                    progressive = (
                        f"DISPOSITIVO_DELIBERA_{device_index}"
                        if len(device_texts) > 1
                        else "DISPOSITIVO_DELIBERA"
                    )
                    rows.append({
                        "id_atto": id_atto,
                        "tool": tool,
                        "source_format": source_format,
                        "ordine_blocco": block_order,
                        "tipo_blocco": "DISPOSITIVO_DELIBERA",
                        "tipo_blocco_progressivo": progressive,
                        "macro_sezione": "DISPOSITIVO_DELIBERA",
                        "is_narrativa": 0,
                        "testo_blocco": device_text,
                        "page_start": row.get("page"),
                        "page_end": terminal_page_end,
                        "bbox_start": row.get("bbox"),
                        "n_elementi": terminal_elements,
                    })
                has_deliberation_terminal = True
                if later_proposal_position is not None:
                    continue_to_followup_proposal = True
                    skip_through_position = max(
                        skip_through_position, terminal_end_position
                    )
                    continue
                break

            # In alcuni modelli ATTESTA sostituisce DELIBERA come dispositivo
            # principale. Non vale per pubblicazione, esecutivita', conformita'
            # o pareri, filtrati sopra.
            if effective_stop == "ATTESTA":
                if current_block is not None:
                    rows.append(current_block)
                    current_block = None
                (
                    terminal_text,
                    terminal_page_end,
                    terminal_elements,
                    _,
                ) = collect_terminal_device_text(
                    group_rows, row_position, "ATTESTA"
                )
                procedural_tail = POST_DEVICE_PROCEDURAL_PATTERN.search(
                    terminal_text
                )
                if procedural_tail is not None:
                    terminal_text = terminal_text[
                        :procedural_tail.start()
                    ].rstrip()
                block_order += 1
                rows.append({
                    "id_atto": id_atto,
                    "tool": tool,
                    "source_format": source_format,
                    "ordine_blocco": block_order,
                    "tipo_blocco": "DISPOSITIVO_ATTESTA",
                    "tipo_blocco_progressivo": "DISPOSITIVO_ATTESTA",
                    "macro_sezione": "DISPOSITIVO_ATTESTA",
                    "is_narrativa": 0,
                    "testo_blocco": terminal_text,
                    "page_start": row.get("page"),
                    "page_end": terminal_page_end,
                    "bbox_start": row.get("bbox"),
                    "n_elementi": terminal_elements,
                })
                has_deliberation_terminal = True
                break

            # DELIBERA/PROPONE sono formule dispositive, non tipi di blocco
            # narrativo. Se compaiono in un'intestazione non dispositiva, come
            # "DELIBERA Numero 62", vanno semplicemente ignorati.
            if marker in {"DELIBERA", "PROPONE_DELIBERARE"}:
                marker = None

            if marker is not None:
                if current_block is not None:
                    rows.append(current_block)

                block_order += 1
                marker_counts[marker] = marker_counts.get(marker, 0) + 1

                current_block = {
                    "id_atto": id_atto,
                    "tool": tool,
                    "source_format": source_format,
                    "ordine_blocco": block_order,
                    "tipo_blocco": marker,
                    "tipo_blocco_progressivo": f"{marker}_{marker_counts[marker]}",
                    "macro_sezione": MACRO_SECTION.get(marker, "ALTRO"),
                    "is_narrativa": int(MACRO_SECTION.get(marker, "").startswith(("NARRATIVA", "PREAMBOLO", "MOTIVAZIONE", "ISTRUTTORIA"))),
                    "testo_blocco": text,
                    "page_start": row.get("page"),
                    "page_end": row.get("page"),
                    "bbox_start": row.get("bbox"),
                    "n_elementi": 1,
                }

            else:
                if current_block is not None and text:
                    current_block["testo_blocco"] += " " + text
                    if pd.notna(row.get("page")):
                        current_block["page_end"] = row.get("page")
                    current_block["n_elementi"] += 1

            if stop_after_row:
                if current_block is not None:
                    rows.append(current_block)
                    current_block = None
                awaiting_deliberation = True
                continue

            if proposal_after_row:
                if current_block is not None:
                    rows.append(current_block)
                    current_block = None
                if continue_to_followup_proposal:
                    (
                        proposal_text,
                        proposal_page_end,
                        proposal_elements,
                        _,
                    ) = collect_terminal_device_text(
                        group_rows, row_position, "PROPONE"
                    )
                    block_order += 1
                    marker_counts["DISPOSITIVO_PROPOSTA"] = (
                        marker_counts.get("DISPOSITIVO_PROPOSTA", 0) + 1
                    )
                    rows.append({
                        "id_atto": id_atto,
                        "tool": tool,
                        "source_format": source_format,
                        "ordine_blocco": block_order,
                        "tipo_blocco": "DISPOSITIVO_PROPOSTA",
                        "tipo_blocco_progressivo": (
                            "DISPOSITIVO_PROPOSTA_"
                            f"{marker_counts['DISPOSITIVO_PROPOSTA']}"
                        ),
                        "macro_sezione": "DISPOSITIVO_PROPOSTA",
                        "is_narrativa": 0,
                        "testo_blocco": proposal_text,
                        "page_start": row.get("page"),
                        "page_end": proposal_page_end,
                        "bbox_start": row.get("bbox"),
                        "n_elementi": proposal_elements,
                    })
                    continue_to_followup_proposal = False
                    break
                block_order += 1
                marker_counts["DISPOSITIVO_PROPOSTA"] = marker_counts.get("DISPOSITIVO_PROPOSTA", 0) + 1
                proposal_text = original_text[int(proposal_pos):].strip()
                proposal_block = {
                    "id_atto": id_atto,
                    "tool": tool,
                    "source_format": source_format,
                    "ordine_blocco": block_order,
                    "tipo_blocco": "DISPOSITIVO_PROPOSTA",
                    "tipo_blocco_progressivo": f"DISPOSITIVO_PROPOSTA_{marker_counts['DISPOSITIVO_PROPOSTA']}",
                    "macro_sezione": "DISPOSITIVO_PROPOSTA",
                    "is_narrativa": 0,
                    "testo_blocco": proposal_text,
                    "page_start": row.get("page"),
                    "page_end": row.get("page"),
                    "bbox_start": row.get("bbox"),
                    "n_elementi": 1,
                }
                skipping_proposal_device = True
                continue

        if current_block is not None:
            rows.append(current_block)
        if proposal_block is not None:
            rows.append(proposal_block)
        if not has_deliberation_terminal:
            block_order += 1
            rows.append({
                "id_atto": id_atto,
                "tool": tool,
                "source_format": source_format,
                "ordine_blocco": block_order,
                "tipo_blocco": "DISPOSITIVO_DELIBERA",
                "tipo_blocco_progressivo": "DISPOSITIVO_DELIBERA",
                "macro_sezione": "DISPOSITIVO_DELIBERA",
                "is_narrativa": 0,
                "testo_blocco": "",
                "page_start": None,
                "page_end": None,
                "bbox_start": None,
                "n_elementi": 0,
            })

    blocks = pd.DataFrame(rows)

    if blocks.empty:
        return blocks

    blocks["testo_blocco"] = blocks["testo_blocco"].apply(normalize_text)
    return blocks


# ============================================================
# 9. FLAG: BLOCCHI SPEZZATI / INVERSIONI
# ============================================================

def add_sequence_flags(blocks: pd.DataFrame) -> pd.DataFrame:
    if blocks.empty:
        return blocks

    blocks = blocks.copy()
    blocks["_id_num"] = pd.to_numeric(
        blocks["id_atto"].astype(str).str.extract(r"(\d+)$")[0], errors="coerce"
    )
    blocks = blocks.sort_values(
        ["_id_num", "id_atto", "tool", "source_format", "ordine_blocco"]
    )

    expected_order = {
        "PREMESSO_E_RILEVATO": 1,
        "PREMESSO": 1,
        "RICHIAMATO": 2,
        "RAMMENTATO": 2,
        "RICORDATO": 2,
        "RICORDANDO": 2,
        "VISTO": 3,
        "VISTI_PARERI": 3,
        "AI_SENSI": 3,
        "VISTI_ED_ACQUISITI": 4,
        "ACQUISITO": 4,
        "ACQUISITI_PARERI": 4,
        "ATTESTA": 4,
        "RILASCIATO": 4,
        "DATO_ATTO": 5,
        "FATTO_PROPRIO": 5,
        "FATTO_PRESENTE": 5,
        "PRESA_VISIONE": 5,
        "PRESO_ATTO": 5,
        "VISTA_E_CONDIVISA": 5,
        "VISTO_E_PRESO_ATTO": 5,
        "AVUTO_RIGUARDO": 5,
        "TENUTO_CONTO": 5,
        "TENUTO_PRESENTE": 5,
        "ASSUNTO": 5,
        "UDITO": 6,
        "DOPO": 6,
        "SU_INVITO": 6,
        "ADEMPIUTO": 6,
        "ESAMINATO": 6,
        "ANALIZZATO": 6,
        "DEPOSITATO": 6,
        "PREDISPOSTO": 6,
        "FORMULATO": 6,
        "INDIVIDUATO": 6,
        "INCARICATO": 6,
        "ACCERTATO": 6,
        "CONSTATATO": 6,
        "APPURATO": 6,
        "RISCONTRATO": 6,
        "AVVISATO": 6,
        "ATTESTATO": 6,
        "VERIFICATO": 6,
        "LETTO_ESAMINATO": 6,
        "LETTO": 6,
        "CONSIDERATO": 7,
        "STANTE": 7,
        "RILEVATO": 7,
        "OSSERVATO": 7,
        "POSTO": 7,
        "ATTESO": 7,
        "RAVVISATO": 7,
        "EVIDENZIATO": 7,
        "RIBADITO": 7,
        "VALUTATO": 8,
        "VALUTATO_OPPORTUNO": 8,
        "RICONOSCIUTO": 8,
        "RITENUTO_NECESSARIO": 9,
        "RITENUTO_OPPORTUNO": 9,
        "RITENUTO": 9,
        "SPECIFICATO": 9,
        "PRECISATO": 9,
        "STABILITO": 9,
    }

    all_rows = []

    for (id_atto, tool, source_format), group in blocks.groupby(
        ["id_atto", "tool", "source_format"], dropna=False, sort=False
    ):
        group = group.copy()
        seq = group["tipo_blocco"].tolist()

        # blocco spezzato: stesso tipo che ricompare dopo un altro tipo
        flags_spezzato = []
        for i, tipo in enumerate(seq):
            previous_same = tipo in seq[:i]
            immediate_previous_same = i > 0 and seq[i - 1] == tipo
            flags_spezzato.append(int(previous_same and not immediate_previous_same))

        group["flag_spezzato"] = flags_spezzato

        # Inversione locale rispetto all'ordine teorico. Un dispositivo della
        # proposta separa due narrative e quindi azzera il confronto.
        inversion = []
        previous_score: Optional[int] = None
        for tipo in seq:
            if tipo == "DISPOSITIVO_PROPOSTA":
                inversion.append(0)
                previous_score = None
                continue
            score = expected_order.get(tipo, 99)
            inversion.append(int(previous_score is not None and score < previous_score))
            previous_score = score

        group["flag_inversione_locale"] = inversion
        all_rows.append(group)

    result = pd.concat(all_rows, ignore_index=True)
    return result.drop(columns=["_id_num"], errors="ignore")


# ============================================================
# 10. REPORT DESCRITTIVO
# ============================================================

def make_reports(elements: pd.DataFrame, blocks: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    elements.to_csv(out_dir / "01_elementi_normalizzati.csv", index=False, encoding="utf-8-sig", sep=";")
    blocks.to_csv(
        out_dir / "02_blocchi_narrativi_esteso_audit.csv",
        index=False,
        encoding="utf-8-sig",
        sep=";",
    )

    final_columns = ["id_delibera", "ordine_globale", "tipo_blocco", "testo_blocco"]
    if blocks.empty:
        final_blocks = pd.DataFrame(columns=final_columns)
    else:
        final_blocks = blocks.copy()
        final_blocks["id_delibera"] = final_blocks["id_atto"].apply(
            lambda value: canonical_document_id(value, default_prefix="INPUT")
        )
        final_blocks["ordine_globale"] = final_blocks["ordine_blocco"]
        final_blocks["tipo_blocco"] = final_blocks["tipo_blocco_progressivo"]
        final_blocks = final_blocks[final_columns]

    final_blocks.to_csv(
        out_dir / "02_blocchi_narrativi.csv", index=False, encoding="utf-8-sig", sep=";"
    )

    if not elements.empty:
        audit = (
            elements
            .groupby(["tool", "source_format", "role_norm"], dropna=False)
            .size()
            .reset_index(name="n")
            .sort_values(["tool", "source_format", "role_norm"])
        )
        audit.to_csv(out_dir / "03_audit_elementi.csv", index=False, encoding="utf-8-sig", sep=";")

        marker_audit = (
            elements[~elements["marker_detected"].isna()]
            .groupby(["tool", "source_format", "marker_detected"], dropna=False)
            .size()
            .reset_index(name="n")
            .sort_values(["tool", "source_format", "marker_detected"])
        )
        marker_audit.to_csv(out_dir / "04_audit_marker_detected.csv", index=False, encoding="utf-8-sig", sep=";")

    if not blocks.empty:
        freq_blocchi = (
            blocks
            .groupby(["tool", "source_format", "tipo_blocco", "macro_sezione"], dropna=False)
            .size()
            .reset_index(name="n")
            .sort_values(["tool", "source_format", "tipo_blocco"])
        )
        freq_blocchi.to_csv(out_dir / "05_frequenze_blocchi.csv", index=False, encoding="utf-8-sig", sep=";")

        sequenze = (
            blocks
            .groupby(["id_atto", "tool", "source_format"])["tipo_blocco"]
            .apply(lambda x: " > ".join(x))
            .reset_index(name="sequenza_blocchi")
        )
        sequenze.to_csv(out_dir / "06_sequenze_blocchi.csv", index=False, encoding="utf-8-sig", sep=";")


# ============================================================
# 11. MAIN
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Segmenta in blocchi narrativi i file PDF, Markdown e JSON delle delibere."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_ROOT,
        help=f"Directory di input (default: {DEFAULT_INPUT_ROOT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUT_ROOT,
        help=f"Directory in cui creare SEGMENTAZIONE_N (default: {DEFAULT_OUT_ROOT})",
    )
    parser.add_argument(
        "--max-documenti",
        type=int,
        default=0,
        help="Numero massimo di PDF da elaborare per ciascun estrattore; 0 = tutti (default: tutti)",
    )
    parser.add_argument(
        "--preserva-nomi-pdf",
        action="store_true",
        help=(
            "Non rinomina o elimina i PDF; richiede nomi GOLD_####.pdf "
            "o INPUT_####.pdf. "
            "Utile per mantenere gli ID di un golden set."
        ),
    )
    parser.add_argument(
        "--prefisso-id",
        choices=("GOLD", "INPUT"),
        help=(
            "Prefisso assegnato quando i PDF vengono rinumerati. "
            "Se omesso viene dedotto dal percorso: GOLD per cartelle golden, "
            "INPUT negli altri casi."
        ),
    )
    parser.add_argument(
        "--marker-disable-ocr",
        action="store_true",
        help=(
            "Per Marker usa solo il testo incorporato nei PDF, evitando il backend OCR/VLM locale. "
            "Piu' veloce e semplice, ma meno adatto ai PDF scansionati."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = args.input.expanduser().resolve()
    output_root = args.output.expanduser().resolve()

    if not input_root.is_dir():
        print(f"Directory di input inesistente o non valida: {input_root}")
        return

    id_prefix = args.prefisso_id or infer_dataset_prefix(input_root)
    pdf_audit = prepare_input_pdfs(
        input_root,
        preserve_names=args.preserva_nomi_pdf,
        id_prefix=id_prefix,
    )
    duplicates = pdf_audit[pdf_audit["duplicato"] == 1].copy()
    duplicate_report = input_root / "00_pdf_duplicati.csv"
    if not duplicates.empty:
        duplicates.to_csv(duplicate_report, index=False, encoding="utf-8-sig", sep=";")

    if not pdf_audit.empty:
        n_duplicates = len(duplicates)
        n_unique = len(pdf_audit) - n_duplicates
        print(
            f"PDF censiti nell'insieme {id_prefix}: {len(pdf_audit)}; "
            f"PDF unici: {n_unique}"
        )
        print(f"PDF duplicati rilevati: {n_duplicates}")

    if not duplicates.empty:
        action = "trovati (non eliminati)" if args.preserva_nomi_pdf else "trovati ed eliminati"
        print(f"\nSono stati {action} questi PDF duplicati:")
        for _, duplicate in duplicates.iterrows():
            print(
                f"- {duplicate['nome_originale']} era identico a "
                f"{duplicate['duplicato_di']} (SHA-256: {duplicate['sha256']})"
            )
        print(f"\nReport salvato in: {duplicate_report}")
        if not args.preserva_nomi_pdf:
            print("I PDF unici sono stati rinumerati; la segmentazione prosegue.")

    out_dir = next_segmentation_dir(output_root)
    unique_pdf_paths = [
        input_root / name
        for name in pdf_audit.loc[pdf_audit["duplicato"] == 0, "nome_assegnato"]
    ]
    if args.max_documenti < 0:
        raise ValueError("--max-documenti non può essere negativo")
    if args.max_documenti > 0:
        unique_pdf_paths = unique_pdf_paths[:args.max_documenti]
    selected_ids = {path.stem for path in unique_pdf_paths}
    print(f"PDF selezionati per l'elaborazione: {len(unique_pdf_paths)}")

    completed_tools: List[str] = []
    for tool_name in EXTRACTOR_TOOLS:
        tool_dir = out_dir / tool_name.upper()
        extraction_dir_for_tool = tool_dir / "_estrazioni"
        tool_dir.mkdir(parents=True, exist_ok=True)
        pdf_audit.to_csv(
            tool_dir / "00_manifest_pdf_input.csv", index=False, encoding="utf-8-sig", sep=";"
        )

        print(f"\nAvvio estrazione con {tool_name}...")
        try:
            if tool_name == "docling":
                extraction_errors = extract_with_docling(
                    unique_pdf_paths, extraction_dir_for_tool
                )
            elif tool_name == "opendataloader":
                extraction_errors = extract_with_opendataloader(
                    unique_pdf_paths, extraction_dir_for_tool
                )
            elif tool_name == "marker":
                extraction_errors = extract_with_marker(
                    unique_pdf_paths,
                    extraction_dir_for_tool,
                    disable_ocr=args.marker_disable_ocr,
                )
            else:
                raise ValueError(f"Estrattore non supportato: {tool_name}")
        except Exception as exc:
            extraction_errors = [{"file": "BATCH", "errore": str(exc)}]
            print(f"[ERRORE {tool_name.upper()}] {exc}")

        if extraction_errors:
            pd.DataFrame(extraction_errors).to_csv(
                tool_dir / "00_errori_estrazione.csv",
                index=False,
                encoding="utf-8-sig",
                sep=";",
            )

        tool_had_output = False
        for selected_format in ("json", "markdown"):
            elements_for_format = read_all_outputs(
                extraction_dir_for_tool, selected_format
            )
            if elements_for_format.empty:
                print(f"Nessun output {selected_format} prodotto da {tool_name}.")
                continue

            elements_for_format = elements_for_format[
                elements_for_format["id_atto"].isin(selected_ids)
            ].copy()

            elements_for_format["text"] = elements_for_format["text"].apply(normalize_text)
            elements_for_format["procedural_stop_pos"] = elements_for_format["text"].apply(
                procedural_stop_position
            )
            elements_for_format["proposal_stop_pos"] = elements_for_format["text"].apply(
                proposal_stop_position
            )
            elements_for_format["stop_detected"] = elements_for_format.apply(
                lambda row: detect_dispositive_stop(
                    row["text"], str(row.get("role_norm", ""))
                ),
                axis=1,
            )
            elements_for_format["marker_detected"] = elements_for_format["text"].apply(detect_marker)
            elements_for_format["is_noise"] = elements_for_format.apply(is_noise, axis=1)
            blocks_for_format = add_sequence_flags(build_blocks(elements_for_format))

            format_dir = tool_dir / selected_format.upper()
            make_reports(elements_for_format, blocks_for_format, format_dir)
            tool_had_output = True
            print(
                f"{tool_name}/{selected_format}: "
                f"{len(elements_for_format)} elementi, {len(blocks_for_format)} blocchi."
            )

        if tool_had_output:
            completed_tools.append(tool_name.upper())

    print("Segmentazione completata per tutti gli estrattori disponibili e i formati.")
    print("Estrattori con output: " + (", ".join(completed_tools) if completed_tools else "nessuno"))
    print(f"Input letto da: {input_root}")
    print(f"Output salvati in: {out_dir}")
    (out_dir / "00_COMPLETATA.txt").write_text(
        "Segmentazione completata. Estrattori con output: "
        + (", ".join(completed_tools) if completed_tools else "nessuno")
        + ".\n",
        encoding="utf-8",
    )
    return

    # Codice storico non raggiungibile, mantenuto temporaneamente per compatibilità.
    extraction_dir = out_dir / "_estrazioni_docling"
    extraction_dir = out_dir / f"_estrazioni_{selected_tool}"
    pdf_audit.to_csv(
        out_dir / "00_manifest_pdf_input.csv", index=False, encoding="utf-8-sig"
    )

    if SELECTED_TOOL != "docling" or SELECTED_FORMAT != "json":
        raise ValueError("Questa esecuzione è configurata per Docling JSON.")

    unique_pdf_paths = [
        input_root / name
        for name in pdf_audit.loc[pdf_audit["duplicato"] == 0, "nome_assegnato"]
    ]
    extract_with_docling(unique_pdf_paths, extraction_dir)
    elements = read_all_outputs(extraction_dir)

    if elements.empty:
        print("Nessun file .pdf, .json o .md letto nella directory di input e nelle sottocartelle.")
        print(f"Cartella esaminata: {input_root}")
        return

    elements["text"] = elements["text"].apply(normalize_text)
    elements["marker_detected"] = elements["text"].apply(detect_marker)
    elements["is_noise"] = elements.apply(is_noise, axis=1)

    blocks = build_blocks(elements)
    blocks = add_sequence_flags(blocks)

    make_reports(elements, blocks, out_dir)

    print("Segmentazione completata.")
    print(f"Elementi letti: {len(elements)}")
    print(f"Blocchi prodotti: {len(blocks)}")
    print(f"Input letto da: {input_root}")
    print(f"Output salvati in: {out_dir}")


if __name__ == "__main__":
    main()
