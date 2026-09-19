// Targeted edit of a single top-level string value in a JSON object body.
//
// The Anthropic-native upstreams accept the client's body almost verbatim; the
// only field the proxy has to change is the top-level `model`. Re-serializing
// the whole parsed object graph costs O(body) CPU on the Workers free plan,
// where the entire per-request budget is 10 ms, so for that one field we splice
// the original text instead and let the runtime encode it once.
//
// Anything unexpected returns null and the caller falls back to the full
// JSON.parse + JSON.stringify path, so this can never produce a wrong body.

// A `model` key past this offset is not worth looking for: Claude Code puts it
// first, and the fallback path is always correct, just slower. The bound also
// keeps a pathological body (one enormous string value) from turning the scan
// into a full O(body) walk.
export const SCAN_LIMIT = 65536;

const QUOTE = 34;
const BACKSLASH = 92;
const COLON = 58;
const COMMA = 44;
const OPEN_BRACE = 123;
const CLOSE_BRACE = 125;
const OPEN_BRACKET = 91;
const CLOSE_BRACKET = 93;

function isJsonWhitespace(code) {
  return code === 32 || code === 9 || code === 10 || code === 13;
}

// `start` points at an opening quote. Returns the index just past the closing
// quote, or -1 when the string is unterminated within `limit`.
function skipString(text, start, limit) {
  for (let i = start + 1; i < limit; i++) {
    const code = text.charCodeAt(i);
    if (code === BACKSLASH) {
      i++;
      continue;
    }
    if (code === QUOTE) return i + 1;
  }
  return -1;
}

/**
 * Replace the value of the top-level `key` in a JSON object, keeping every
 * other byte of `text` untouched.
 *
 * Returns the new text, or null when the value cannot be located or replaced
 * unambiguously (not an object, key absent or past `limit`, value not a string,
 * unterminated string, root object closed first). Callers must treat null as
 * "use the normal parse path".
 */
export function spliceTopLevelString(text, key, newValue, limit = SCAN_LIMIT) {
  if (typeof text !== "string" || typeof key !== "string") return null;
  const replacement = JSON.stringify(newValue);
  if (replacement === undefined) return null;

  const scanEnd = Math.min(text.length, limit);
  let i = 0;
  while (i < scanEnd && isJsonWhitespace(text.charCodeAt(i))) i++;
  if (i >= scanEnd || text.charCodeAt(i) !== OPEN_BRACE) return null;

  const keyLiteral = JSON.stringify(key);
  let depth = 1;
  // At depth 1 a string is a key right after `{` or `,`, and a value otherwise.
  let expectKey = true;

  for (i++; i < scanEnd; i++) {
    const code = text.charCodeAt(i);

    if (code === QUOTE) {
      const end = skipString(text, i, scanEnd);
      if (end === -1) return null;
      if (depth === 1 && expectKey) {
        if (text.startsWith(keyLiteral, i)) {
          let j = end;
          while (j < scanEnd && isJsonWhitespace(text.charCodeAt(j))) j++;
          if (text.charCodeAt(j) !== COLON) return null;
          j++;
          while (j < scanEnd && isJsonWhitespace(text.charCodeAt(j))) j++;
          if (text.charCodeAt(j) !== QUOTE) return null;
          const valueEnd = skipString(text, j, scanEnd);
          if (valueEnd === -1) return null;
          return text.slice(0, j) + replacement + text.slice(valueEnd);
        }
        expectKey = false;
      }
      i = end - 1;
      continue;
    }

    if (code === OPEN_BRACE || code === OPEN_BRACKET) {
      depth++;
      continue;
    }
    if (code === CLOSE_BRACE || code === CLOSE_BRACKET) {
      depth--;
      if (depth === 0) return null;
      continue;
    }
    if (code === COMMA) {
      if (depth === 1) expectKey = true;
      continue;
    }
  }

  return null;
}
