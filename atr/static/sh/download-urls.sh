#!/bin/sh
set -efu

_url_of_urls="[URL_OF_URLS]"
_curl_extra="[CURL_EXTRA]"

# shellcheck disable=SC2086
curl ${_curl_extra} -fsS "$_url_of_urls" | while IFS= read -r _url_and_path
do
  [ -n "$_url_and_path" ] || continue
  _url=${_url_and_path%% *}
  _path=${_url_and_path#* }

  printf "Downloading %s\n" "$_path" || :
  # shellcheck disable=SC2086
  curl ${_curl_extra} --location --proto-redir =https --create-dirs -fsS "$_url" -o "$_path"
done
