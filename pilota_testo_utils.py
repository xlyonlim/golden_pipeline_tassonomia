from __future__ import annotations

import re
from pathlib import Path


PAROLE_CHIAVE_ATTO = [
    "delibera",
    "deliberazione",
    "determina",
    "determinazione",
    "decreto",
    "ordinanza",
    "regolamento",
    "statuto",
    "accordo",
    "convenzione",
    "oggetto",
    "visto",
    "considerato",
    "ritenuto",
    "premesso",
    "delibera",
    "determina",
]


def pdf_files(input_dir: Path) -> list[Path]:
    return sorted(
        [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"],
        key=lambda path: natural_key(path.name),
    )


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def qualita_testo(testo: str) -> int:
    if not testo:
        return 0

    testo_pulito = re.sub(r"\s+", " ", testo).strip()
    if not testo_pulito:
        return 0

    lettere = len(re.findall(r"[^\W\d_]", testo_pulito, flags=re.UNICODE))
    simboli = len(re.findall(r"[^\w\s]", testo_pulito, flags=re.UNICODE))
    rapporto_lettere = lettere / max(len(testo_pulito), 1)
    rapporto_simboli = simboli / max(len(testo_pulito), 1)
    parole_chiave = sum(1 for parola in PAROLE_CHIAVE_ATTO if parola in testo_pulito.lower())

    score = 0
    score += min(40, len(testo_pulito) // 90)
    score += min(25, int(rapporto_lettere * 100 / 2.5))
    score -= min(20, int(rapporto_simboli * 100))
    score += min(25, parole_chiave * 4)
    return max(0, min(100, score))


def testo_sufficiente(testo: str, min_caratteri: int = 500, min_score: int = 70) -> bool:
    return len(re.sub(r"\s+", " ", testo or "").strip()) >= min_caratteri and qualita_testo(testo) >= min_score


def pulisci_testo_atto(testo: str) -> str:
    """Rimuove allegati/rumore tipici, preservando i blocchi narrativi."""
    if not testo:
        return ""

    testo = correggi_mojibake(testo)
    testo = testo.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    testo = rimuovi_tabelle_markdown(testo)

    righe = [normalizza_spazi_riga(riga) for riga in testo.splitlines()]
    blocchi: list[str] = []
    corrente: list[str] = []
    in_blocco_da_scartare = False

    for riga in righe:
        if not riga:
            if corrente:
                blocco = "\n".join(corrente).strip()
                if blocco and not blocco_da_scartare(blocco):
                    blocchi.append(blocco)
                corrente = []
            in_blocco_da_scartare = False
            continue

        if inizio_coda_firme_o_pubblicazione(riga):
            if corrente:
                blocco = "\n".join(corrente).strip()
                if blocco and not blocco_da_scartare(blocco):
                    blocchi.append(blocco)
            break

        if inizio_sezione_da_scartare(riga):
            if corrente:
                blocco = "\n".join(corrente).strip()
                if blocco and not blocco_da_scartare(blocco):
                    blocchi.append(blocco)
                corrente = []
            in_blocco_da_scartare = True
            continue

        if in_blocco_da_scartare:
            if inizio_blocco_narrativo(riga):
                in_blocco_da_scartare = False
            else:
                continue

        if riga_da_scartare(riga):
            continue

        corrente.append(riga)

    if corrente:
        blocco = "\n".join(corrente).strip()
        if blocco and not blocco_da_scartare(blocco):
            blocchi.append(blocco)

    blocchi_puliti = [ricomponi_blocco_narrativo(blocco) for blocco in blocchi]
    testo_finale = "\n\n".join(blocco for blocco in blocchi_puliti if blocco).strip()
    testo_finale = re.sub(r"\n{3,}", "\n\n", testo_finale)
    return testo_finale


def correggi_mojibake(testo: str) -> str:
    indicatori = ["Ã", "Â", "â€™", "â€œ", "â€", "ï¿½"]
    if not any(indicatore in testo for indicatore in indicatori):
        return testo
    try:
        corretto = testo.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return testo
    return corretto if corretto.count("Ã") < testo.count("Ã") else testo


def normalizza_spazi_riga(riga: str) -> str:
    riga = re.sub(r"[ \u00a0]+", " ", riga or "")
    riga = re.sub(r"^[|:;,\-. ]+", "", riga)
    return riga.strip()


def rimuovi_tabelle_markdown(testo: str) -> str:
    righe = testo.splitlines()
    pulite = []
    in_tabella = False

    for riga in righe:
        stripped = riga.strip()
        sembra_tabella = stripped.startswith("|") and stripped.endswith("|")
        separatore_md = bool(re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", stripped))

        if sembra_tabella or separatore_md:
            in_tabella = True
            continue

        if in_tabella and stripped:
            celle = [c.strip() for c in re.split(r"\s{2,}|\|", stripped) if c.strip()]
            if len(celle) >= 3:
                continue

        in_tabella = False
        pulite.append(riga)

    return "\n".join(pulite)


def inizio_sezione_da_scartare(riga: str) -> bool:
    r = riga.lower()
    pattern = [
        r"^certificat[oa] di pubblicazione\b",
        r"^relata di pubblicazione\b",
        r"^attestazione di (esecutivit|pubblicazione)\b",
        r"^referto di pubblicazione\b",
        r"^visto di regolarit[àa] contabile\b",
        r"^parere di regolarit[àa] contabile\b",
        r"^elenco (dei )?(presenti|assenti)\b",
        r"^presenti\s*[:\-]",
        r"^assenti\s*[:\-]",
        r"^componenti presenti\b",
        r"^componenti assenti\b",
        r"^assegnati\s+n\.",
        r"^la presente (deliberazione|determinazione).*(pubblicata|affissa)",
    ]
    return any(re.search(p, r) for p in pattern)


def inizio_coda_firme_o_pubblicazione(riga: str) -> bool:
    r = riga.lower()
    pattern = [
        r"^il presente verbale viene letto",
        r"^il sottoscritto, visti gli atti",
        r"^la sottoscritta, visti gli atti",
        r"^certificat[oa] di pubblicazione\b",
        r"^relata di pubblicazione\b",
        r"^attestazione di esecutivit",
    ]
    return any(re.search(p, r) for p in pattern)


def riga_da_scartare(riga: str) -> bool:
    r = riga.lower()
    pattern = [
        r"firma digitale",
        r"firmato digitalmente",
        r"documento informatico firmato",
        r"copia conforme",
        r"impronta\s*:",
        r"\bhash\b",
        r"codice verific",
        r"protocollo informatico",
        r"marca temporale",
        r"^\s*il (segretario|sindaco|responsabile|dirigente|presidente)\b.*$",
        r"^\s*f\.to\b",
        r"^\s*firmat[oa]\b",
        r"^\s*totale\s+",
        r"^\s*(capitolo|impegno|accertamento|importo|iva|cig|cup)\b",
    ]
    if any(re.search(p, r) for p in pattern):
        return True

    celle = [c for c in re.split(r"\s{2,}|\|", riga) if c.strip()]
    numeri = len(re.findall(r"\b\d+[,.]?\d*\b", riga))
    euro = len(re.findall(r"€|\beuro\b", r))
    if len(celle) >= 4 and (numeri >= 2 or euro):
        return True

    lettere = len(re.findall(r"[^\W\d_]", r, flags=re.UNICODE))
    simboli = len(re.findall(r"[^\w\s]", r, flags=re.UNICODE))
    return lettere < 4 and simboli > lettere


def blocco_da_scartare(blocco: str) -> bool:
    b = re.sub(r"\s+", " ", blocco.lower()).strip()
    if not b:
        return True
    pattern = [
        r"certificat[oa] di pubblicazione",
        r"relata di pubblicazione",
        r"attestazione di esecutivit",
        r"elenco (dei )?(presenti|assenti)",
        r"componenti (presenti|assenti)",
        r"firmato digitalmente",
        r"firma digitale",
    ]
    if any(re.search(p, b) for p in pattern):
        return True

    righe = [r for r in blocco.splitlines() if r.strip()]
    righe_tabellari = 0
    for riga in righe:
        celle = [c for c in re.split(r"\s{2,}|\|", riga) if c.strip()]
        numeri = len(re.findall(r"\b\d+[,.]?\d*\b", riga))
        if len(celle) >= 4 or numeri >= 4:
            righe_tabellari += 1
    return bool(righe) and righe_tabellari / max(len(righe), 1) >= 0.60


def inizio_blocco_narrativo(riga: str) -> bool:
    r = riga.lower()
    return bool(
        re.match(
            r"^(oggetto|premesso|visto|vista|viste|visti|considerato|ritenuto|dato atto|delibera|determina|ordina|decreta)\b",
            r,
        )
    )


def ricomponi_blocco_narrativo(blocco: str) -> str:
    righe = [r.strip() for r in blocco.splitlines() if r.strip()]
    if not righe:
        return ""

    output = [righe[0]]
    for riga in righe[1:]:
        precedente = output[-1]
        if deve_unire_riga(precedente, riga):
            output[-1] = f"{precedente} {riga}"
        else:
            output.append(riga)
    return "\n".join(output).strip()


def deve_unire_riga(precedente: str, riga: str) -> bool:
    if not precedente or not riga:
        return False
    if inizio_blocco_narrativo(riga):
        return False
    if re.match(r"^[-*•]\s+", riga):
        return False
    if re.match(r"^\d+[.)]\s+", riga):
        return False
    if precedente.endswith((".", ";", ":", "?", "!")):
        return False
    if riga[:1].islower():
        return True
    if precedente.endswith(","):
        return True
    return len(precedente) < 100 and not precedente.endswith(".")


def salva_testo(path: Path, testo: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(testo.strip() + "\n", encoding="utf-8")
