"""
BibTeX post-processor: normalize booktitle/journal fields to short names using CCF CSV rules.

For ACM / IEEE / IEEE/CVF / USENIX venues:
  - Conferences:  booktitle={Proc. ACM CCS}, booktitle={Proc. IEEE INFOCOM}
  - Journals:     journal={IEEE TMC}, journal={ACM TOCS}
For other publishers: just the short name from CSV.

Usage:
  python bibtex_normalizer.py input.bib
  python bibtex_normalizer.py input.bib --csv ccf_2026_journals_conferences.csv
"""

import argparse
import csv
import re
import sys
from difflib import SequenceMatcher


def load_csv(csv_path):
    """Load CCF CSV and return list of records."""
    records = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)
    return records


def clean_bibtex_string(s):
    """Remove BibTeX braces, extra whitespace, and common prefixes/noise words."""
    s = s.strip()
    # Remove outer braces (bibtex field values are often wrapped in braces)
    while s.startswith('{') and s.endswith('}'):
        s = s[1:-1].strip()
    # Remove inner braces
    s = s.replace('{', '').replace('}', '')
    # Remove noise prefixes
    s = re.sub(r'^(?:Proceedings|Proc\.?)\s+of\s+the\s+(?:\d{4}\s+)?', '', s, flags=re.IGNORECASE)
    s = re.sub(r'^(?:Proceedings|Proc\.?)\s+', '', s, flags=re.IGNORECASE)
    # Remove " XXXX -" year-dash patterns
    s = re.sub(r'\s*\d{4}\s*[-–]\s*', ' ', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def tokenize(s):
    """Lowercase word tokens from a string (words with 2+ letters)."""
    return set(re.findall(r'[a-zA-Z]{2,}', s.lower()))


def longest_common_substring_ratio(a, b):
    """Ratio of longest common substring to the shorter string length."""
    if not a or not b:
        return 0.0
    match = SequenceMatcher(None, a.lower(), b.lower()).find_longest_match()
    return match.size / min(len(a), len(b))


def match_score(cleaned_bib_str, record, entry_type):
    """Compute a match score between a cleaned bibtex string and a CSV record.

    Returns an integer score (higher = better match). Negative means type mismatch.
    """
    csv_type = record['类型']
    if entry_type == 'inproceedings' and csv_type != '会议':
        return -1
    if entry_type == 'article' and csv_type != '期刊':
        return -1

    short_name = record['期刊简称'].strip()
    full_name = record['期刊全称'].strip()
    bib_lower = cleaned_bib_str.lower()
    full_lower = full_name.lower()

    # Strategy 1: short name as a standalone word/acronym in bib string
    short_pattern = re.compile(r'\b' + re.escape(short_name) + r'\b', re.IGNORECASE)
    if short_pattern.search(cleaned_bib_str):
        return 1000 + len(short_name)

    # Strategy 2: full name is exact substring of cleaned bib string
    if full_lower in bib_lower:
        return 950

    # Strategy 3: cleaned bib string is exact substring of full name
    if bib_lower in full_lower:
        return 920

    # Strategy 4: token overlap (Jaccard-like, weighted to recall)
    full_tokens = tokenize(full_name)
    bib_tokens = tokenize(cleaned_bib_str)
    token_overlap = len(full_tokens & bib_tokens)
    if full_tokens and bib_tokens and token_overlap >= 2:
        recall = token_overlap / len(full_tokens)
        jaccard = token_overlap / len(full_tokens | bib_tokens)
        # Blend recall and jaccard, reward high recall
        score = 800 + int(recall * 100) + int(jaccard * 50)
        return score

    # Strategy 5: longest common substring ratio
    lcs_ratio = longest_common_substring_ratio(full_name, cleaned_bib_str)
    if lcs_ratio > 0.5:
        return 700 + int(lcs_ratio * 200)

    # Strategy 6: full SequenceMatcher ratio
    ratio = SequenceMatcher(None, full_lower, bib_lower).ratio()
    if ratio > 0.5:
        return int(ratio * 500)

    # Strategy 7: check if short name letters appear in order as initials
    # e.g. "TMC" → "Transactions on Mobile Computing" → initials T, M, C match
    bib_words = re.findall(r'[a-zA-Z]{2,}', cleaned_bib_str)
    bib_initials = ''.join(w[0] for w in bib_words).upper()
    if len(short_name) >= 3 and short_name.upper() in bib_initials:
        return 600

    return 0


def find_best_match(bibtex_str, records, entry_type):
    """Find the best matching CSV record for a bibtex string."""
    cleaned = clean_bibtex_string(bibtex_str)
    best_score = 0
    best_record = None

    for record in records:
        score = match_score(cleaned, record, entry_type)
        if score > best_score:
            best_score = score
            best_record = record

    return best_record, best_score


def extract_publisher_prefix(full_name, publisher_field):
    """Extract publisher abbreviation for short-name formatting.

    Priority:
      1. Check if 期刊全称 (full_name) starts with a known publisher (ACM, IEEE/CVF, IEEE, USENIX).
      2. Fall back to the 出版社 (publisher_field).

    Returns the canonical abbreviation, or empty string if no known publisher.
    """
    KNOWN = ['IEEE/CVF', 'IEEE', 'ACM', 'USENIX']
    full_name = full_name.strip()
    full_upper = full_name.upper()
    for pub in KNOWN:
        if full_upper.startswith(pub.upper()):
            return pub
    pub_field = publisher_field.strip().upper()
    for pub in KNOWN:
        if pub_field == pub.upper() or pub_field.startswith(pub.upper()):
            return pub
    return ''


def format_short_name(record, entry_type):
    """Build the short-form booktitle/journal value from a matched CSV record."""
    short_name = record['期刊简称'].strip()
    publisher = extract_publisher_prefix(record['期刊全称'], record['出版社'])
    if publisher:
        if entry_type == 'inproceedings':
            return f'Proc. {publisher} {short_name}'
        else:
            return f'{publisher} {short_name}'
    return short_name


def parse_bibtex_entries(content):
    """Parse bibtex content into a list of entry dicts.

    Each dict has: type, key, raw (full text from @ to closing }), start, end.
    """
    entries = []
    # Match @type{key, ...} — handle nested braces
    pattern = re.compile(r'@(\w+)\s*\{\s*([^,\s]+)\s*,', re.DOTALL)

    for m in pattern.finditer(content):
        entry_type = m.group(1).lower()
        key = m.group(2)
        start = m.start()

        # Find the matching closing brace (opening { is in the matched text)
        brace_start = m.start() + m.group(0).index('{')
        depth = 0
        i = brace_start
        while i < len(content):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    break
            i += 1

        end = i + 1
        raw = content[start:end]

        entries.append({
            'type': entry_type,
            'key': key,
            'raw': raw,
            'start': start,
            'end': end,
        })

    return entries


def process_bibtex(content, records):
    """Process bibtex content and normalize booktitle/journal fields to short names."""
    entries = parse_bibtex_entries(content)
    target_field = {'inproceedings': 'booktitle', 'article': 'journal'}

    replacements = []  # (start, end, replacement_text)
    stats = {'matched': 0, 'unmatched': 0}

    # Pattern to match field_name = {...} handling one level of nested braces
    # Group 1: field_name and equals sign, Group 2: value inside braces
    field_value_pattern = re.compile(
        r'(\w+\s*=\s*)\{((?:[^{}]|\{[^{}]*\})*)\}',
        re.DOTALL
    )

    for entry in entries:
        etype = entry['type']
        if etype not in target_field:
            continue

        field_name = target_field[etype]

        # Find all field=value pairs and locate the target field
        for fm in field_value_pattern.finditer(entry['raw']):
            fname = fm.group(1).strip().rstrip('=').strip().lower()
            if fname != field_name:
                continue

            fvalue = fm.group(2).strip()

            best_record, best_score = find_best_match(fvalue, records, etype)

            if best_record and best_score >= 600:
                new_value = format_short_name(best_record, etype)

                # Reconstruct: field_name = {new_value}
                prefix = fm.group(1)  # e.g. "booktitle = "
                new_field = f'{prefix}{{{new_value}}}'

                # Replace in entry raw text
                new_raw = entry['raw'][:fm.start()] + new_field + entry['raw'][fm.end():]
                replacements.append((entry['start'], entry['end'], new_raw))
                stats['matched'] += 1
            else:
                stats['unmatched'] += 1
                print(
                    f"  Warning: no match for @{etype}{{{entry['key']}}}: "
                    f"{field_name}={{{fvalue[:80]}...}}",
                    file=sys.stderr
                )
            break  # Only process the first matching field

    # Apply replacements in reverse order to preserve positions
    replacements.sort(key=lambda r: r[0], reverse=True)
    result = content
    for start, end, new_raw in replacements:
        result = result[:start] + new_raw + result[end:]

    return result, stats


def main():
    parser = argparse.ArgumentParser(
        description='Normalize BibTeX booktitle/journal fields to short names using CCF CSV rules.'
    )
    parser.add_argument('input', help='Input BibTeX file path')
    parser.add_argument(
        '--csv', default='ccf_2026_journals_conferences.csv',
        help='Path to CCF CSV file'
    )
    parser.add_argument(
        '--output', '-o', default=None,
        help='Output file path (default: input_normalized.bib)'
    )
    args = parser.parse_args()

    records = load_csv(args.csv)
    print(f"Loaded {len(records)} records from {args.csv}")

    with open(args.input, 'r', encoding='utf-8') as f:
        content = f.read()

    result, stats = process_bibtex(content, records)

    if args.output:
        output_path = args.output
    else:
        base = args.input.rsplit('.', 1)[0]
        output_path = f'{base}_normalized.bib'

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result)

    print(f"Matched: {stats['matched']}, Unmatched: {stats['unmatched']}")
    print(f"Output written to: {output_path}")


if __name__ == '__main__':
    main()
