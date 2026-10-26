"""Lossless recovery of a few completed author-output formatting mistakes.

Never invent a string, value or key. The HTTP envelope stays strict JSON; this
applies only to an assistant message whose finish_reason is already 'stop'.
"""
import hashlib
import json
import re


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError('non-finite JSON number')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def _completed_strings(text):
    """Read exactly the three complete strings; only dangling punctuation may
    follow them. An unfinished body or an additional field cannot be recovered.
    """
    decoder = json.JSONDecoder(strict=False)
    if not text.startswith('{'):
        return None
    position, fields = 1, {}
    try:
        while len(fields) < 3:
            while position < len(text) and text[position].isspace(): position += 1
            key, position = decoder.raw_decode(text, position)
            if key not in ('title', 'deck', 'body') or key in fields:
                return None
            while position < len(text) and text[position].isspace(): position += 1
            if position >= len(text) or text[position] != ':': return None
            position += 1
            while position < len(text) and text[position].isspace(): position += 1
            value, position = decoder.raw_decode(text, position)
            if not isinstance(value, str): return None
            fields[key] = value
            if len(fields) < 3:
                while position < len(text) and text[position].isspace(): position += 1
                if position >= len(text) or text[position] != ',': return None
                position += 1
    except (ValueError, TypeError):
        return None
    tail = re.sub(r'\s+', '', text[position:])
    return fields if tail in ('}', ',}', ',"}', '') else None


def _close_arrays_before_objects(text):
    """Legacy nested output sometimes omits a paragraph-array closing ']'.
    Insert only that delimiter, immediately before an explicit closing '}'.
    All original string tokens and all other characters are copied verbatim.
    """
    decoder = json.JSONDecoder(); stack, out = [], []
    position, inserted = 0, 0
    while position < len(text):
        char = text[position]
        if char == '"':
            try:
                _, end = decoder.raw_decode(text, position)
            except ValueError:
                return None
            out.append(text[position:end]); position = end; continue
        if char in '{[':
            stack.append(char)
        elif char == '}':
            while stack and stack[-1] == '[':
                out.append(']'); stack.pop(); inserted += 1
            if not stack or stack.pop() != '{': return None
        elif char == ']':
            if not stack or stack.pop() != '[': return None
        out.append(char); position += 1
    return ''.join(out) if inserted and not stack else None


def decode_article(text):
    try:
        return strict_json(text), None
    except json.JSONDecodeError as original:
        # Duplicate keys/non-finite numbers raise ValueError, not this branch.
        candidate = text.strip().removeprefix('\ufeff').strip()
        # A complete JSON object occasionally has an HTML line-break token
        # appended. Only these exact formatting tags may be removed.
        candidate = re.sub(r'(?:\s*</?br\s*/?>\s*)+$', '', candidate, flags=re.I).strip()
        fenced = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*)\r?\n```', candidate, re.S | re.I)
        if fenced:
            candidate = fenced[1].strip()
        try:
            value, method = strict_json(candidate), 'outer_envelope/1'
        except json.JSONDecodeError:
            value = _completed_strings(candidate)
            method = 'complete_string_fields/1'
            if value is None:
                fixed = _close_arrays_before_objects(candidate)
                if fixed is None: raise original
                value, method = strict_json(fixed), 'close_paragraph_arrays/1'
        return value, {'method': method, 'original_content_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                       'content_values_invented': False}
