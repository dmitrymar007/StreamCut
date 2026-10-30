# Публикация локального релиза 0.3.1

Исправленный релиз подготовлен локально. Никакие изменения на GitHub не опубликованы.

В PowerShell:

```powershell
Set-Location 'E:\Games\repoStreamCut\StreamCut'
& '.\local\release-tools\gh\bin\gh.exe' auth login --hostname github.com --git-protocol https --web
.\scripts\publish_release.ps1
```

Первый вход нужно подтвердить в браузере. Если выполнение скриптов запрещено политикой PowerShell, последнюю команду можно заменить запуском отдельного процесса:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish_release.ps1
```

Скрипт проверит чистоту main, исходный коммит сборки и контрольные суммы, отправит main и тег v0.3.1, создаст новый релиз с описанием из `docs/RELEASE-0.3.1.md` и загрузит пять файлов из `release`. Существующий релиз v0.3.0 не удаляется и не перезаписывается.

## Ручной вариант

После входа в GitHub CLI:

```powershell
git push origin main
git tag v0.3.1
git push origin v0.3.1
& '.\local\release-tools\gh\bin\gh.exe' release create v0.3.1 `
  '.\release\StreamCut-Setup-0.3.1-windows-x64.exe' `
  '.\release\StreamCut-0.3.1-windows-x64.zip' `
  '.\release\StreamCut-0.3.1-source.zip' `
  '.\release\BUILDINFO.json' '.\release\SHA256SUMS.txt' `
  --repo dmitrymar007/StreamCut --verify-tag --latest `
  --title 'StreamCut 0.3.1 - Windows / NVIDIA / CPU' `
  --notes-file '.\docs\RELEASE-0.3.1.md'
```

Если тег уже существует, повторно создавать его не нужно. Не перемещайте существующий тег на другой коммит.
