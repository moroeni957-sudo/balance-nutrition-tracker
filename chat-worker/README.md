# Локальный Codex-worker

Worker каждые 10 секунд проверяет папку `Codex Chat Queue` на Google Drive, запускает локальный `codex exec` в режиме только для чтения и создаёт `answer_<id>.json`.

## Первый запуск

1. В Google Cloud Console создайте OAuth client типа **Desktop app** и скачайте JSON.
2. Сохраните файл как `chat-worker/credentials.local.json`.
3. Выполните `npm install` в папке `chat-worker`.
4. Выполните `npm start`. При первом запуске откроется Google OAuth; после согласия локально появится `token.local.json`.

`credentials.local.json` и `token.local.json` исключены из Git и не должны публиковаться.
