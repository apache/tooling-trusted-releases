#!/bin/sh
set -eu

_purge() {
  _tmpdir=$1
  # Use GNUPGHOME to ensure that child processes are affected too
  GNUPGHOME="$_tmpdir" gpgconf --kill all >/dev/null 2>&1 || :
  rm -rf "$_tmpdir"
}

_mktmpd() (
  # mktmp -d is not in POSIX, and is unportable in practice
  umask 077
  _tmp=${TMPDIR:-/tmp}
  # $TMPDIR can end with a slash
  _tmp=${_tmp%/}
  # POSIX does not guarantee the numeric precision of awk rand()
  # Nor does it guarantee the conversion range of %d in awk printf
  # We scale here by max(i32) as a relatively conservative bound
  _pseudorandom=$(awk 'BEGIN {srand(); printf "%06d\n", int(rand()*2147483647)}')
  _tmpdir="$_tmp/gpgsign-$$-$_pseudorandom"
  mkdir "$_tmpdir" || exit 1
  printf '%s\n' "$_tmpdir"
)

_issue() (
  set -eu
  _name="$1"
  _email="$2"
  _pubfile="$3"
  _prvfile="$4"
  # This is a double check, in case GPG is inconsistent between versions
  if [ -e "$_pubfile" ]
  then
    echo "Error: public key file already exists: $_pubfile" >&2
    exit 1
  fi
  # This introduces a race condition
  # But we can't use --batch --no --output FILE
  # If we do, --export-secret-keys (but not --export) fails silently
  # This happens even without --quiet
  if [ -e "$_prvfile" ]
  then
    echo "Error: private key file already exists: $_prvfile" >&2
    exit 1
  fi
  _tmpdir=$(_mktmpd)
  trap '_purge "$_tmpdir"' EXIT HUP INT TERM
  # --quick-gen-key was added in 2.1.0
  GNUPGHOME="$_tmpdir" gpg --quiet --batch --pinentry-mode loopback --passphrase '' \
    --quick-gen-key "$_name <$_email>" rsa4096 sign never
  GNUPGHOME="$_tmpdir" gpg --quiet --batch --no --armor --export --output "$_pubfile"
  umask 077
  GNUPGHOME="$_tmpdir" gpg --quiet --armor --export-secret-keys > "$_prvfile"
  _fpr=$(GNUPGHOME="$_tmpdir" gpg --quiet --list-keys --with-colons | awk -F: '/^fpr:/ {print $10; exit}')
  echo "Created key pair $_fpr"
)

_sign() (
  set -eu
  _keyfile="$1"
  _infile="$2"
  _outfile="${3:-$2.asc}"
  _tmpdir=$(_mktmpd)
  trap '_purge "$_tmpdir"' EXIT HUP INT TERM
  GNUPGHOME="$_tmpdir" gpg --quiet --batch --import "$_keyfile"
  # --pinentry-mode loopback was added in 2.1.0
  GNUPGHOME="$_tmpdir" gpg --quiet --batch --pinentry-mode loopback --passphrase '' \
    --armor --detach-sign --output "$_outfile" "$_infile"
)

_verify() (
  set -eu
  _keyfile="$1"
  _infile="$2"
  _sigfile="${3:-$2.asc}"
  _tmpdir=$(_mktmpd)
  trap '_purge "$_tmpdir"' EXIT HUP INT TERM
  GNUPGHOME="$_tmpdir" gpg --quiet --batch --import "$_keyfile"
  GNUPGHOME="$_tmpdir" gpg --quiet --batch --verify "$_sigfile" "$_infile"
)

if ! command -v gpg >/dev/null 2>&1
then
  echo "Error: gpg not found" >&2
  exit 1
fi

if ! command -v gpgconf >/dev/null 2>&1
then
  echo "Error: gpgconf not found" >&2
  exit 1
fi

# Validate the GPG version
# GPG gives output like "gpg (GnuPG) 2.4.9"
# NR==1 is like head -n1, and $NF is the last field (NF = Number of Fields)
_gpg_version=$(gpg --version 2>/dev/null | awk 'NR==1 {print $NF; exit}')

# Require x.y or x.y.z
case $_gpg_version in
  [0-9]*.[0-9]*|[0-9]*.[0-9]*.[0-9]*)
    ;;
  *)
    echo "Error: unable to determine gpg version" >&2
    exit 1
    ;;
esac

# %% here effectively means get the first .
_gpg_major=${_gpg_version%%.*}
_gpg_rest=${_gpg_version#*.}
_gpg_minor=${_gpg_rest%%.*}

# Require major and minor to be numbers
case $_gpg_major in ''|*[!0-9]*)
  echo "Error: unable to determine gpg version" >&2
  exit 1
esac
case $_gpg_minor in ''|*[!0-9]*)
  echo "Error: unable to determine gpg version" >&2
  exit 1
esac

# Require gpg 2.1 or higher
if [ "$_gpg_major" -lt 2 ] || { [ "$_gpg_major" -eq 2 ] && [ "$_gpg_minor" -lt 1 ]; }
then
  echo "Error: gpg 2.1 or higher required (found $_gpg_version)" >&2
  exit 1
fi

case "${1:-}" in
  issue)
    if [ $# -ne 5 ]
    then
      echo "Usage: $0 issue NAME EMAIL PUBLIC-KEY-FILE PRIVATE-KEY-FILE" >&2
      echo "Example: $0 issue 'Alice Example' example@apache.org pub.asc prv.asc" >&2
      exit 2
    fi
    _issue "$2" "$3" "$4" "$5"
    ;;
  sign)
    if [ $# -lt 3 ] || [ $# -gt 4 ]
    then
      echo "Usage: $0 sign PRIVATE-KEY-FILE FILE [ SIGNATURE-FILE ]" >&2
      echo "Example: $0 sign prv.asc file.txt" >&2
      exit 2
    fi
    _sign "$2" "$3" "${4:-}"
    ;;
  verify)
    if [ $# -lt 3 ] || [ $# -gt 4 ]
    then
      echo "Usage: $0 verify PUBLIC-KEY-FILE FILE [ SIGNATURE-FILE ]" >&2
      echo "Example: $0 verify pub.asc file.txt" >&2
      exit 2
    fi
    _verify "$2" "$3" "${4:-}"
    ;;
  *)
    echo "Usage: $0 ( issue | sign | verify ) ARGS..." >&2
    echo "Example: $0 issue 'Alice Example' example@apache.org pub.asc prv.asc" >&2
    echo "Example: $0 sign prv.asc file.txt" >&2
    echo "Example: $0 verify pub.asc file.txt" >&2
    exit 2
    ;;
esac
