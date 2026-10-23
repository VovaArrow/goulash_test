# Текущее размещение на VPS

Приложение доступно по адресу http://176.57.218.216. Используется Apache
как reverse proxy к Uvicorn на `127.0.0.1:8000` и отдельный пользователь
`goulash`. Сервис `goulash.service` включён в автозагрузку systemd.

- Код: `/opt/goulash/app`, Python 3.12 и зависимости из `uv.lock`.
- База и сессии: `/var/lib/goulash`, вне каталога кода.
- Переменные: `/etc/goulash.env`, пример — `deploy/goulash.env.example`.
- Конфигурации сервиса и Apache: `deploy/goulash.service` и
  `deploy/apache-goulash.conf`.
- Логи: `journalctl -u goulash`; перезапуск: `systemctl restart goulash`.

Для обновления загрузите архив исходников в `/opt/goulash/app`, выполните
`UV_PYTHON_INSTALL_DIR=/opt/goulash/python /opt/goulash/tools/uv sync --frozen --no-dev --no-install-project --python 3.12`
из этого каталога и перезапустите сервис. Не удаляйте `/var/lib/goulash`.

Доступ по IP сейчас использует HTTP. При подключении домена и HTTPS
обновите виртуальный хост Apache и `GOULASH_PUBLIC_ORIGIN`, затем
перезапустите сервис.

PitchAI временно отключён по запросу владельца: вместо его сайта Apache
возвращает 503, MySQL остановлен и исключён из автозагрузки, PHP 8.1
отключён. Apache переключён с `mpm_prefork` на `mpm_event`.
Файлы сайта, база MySQL и исходные виртуальные хосты сохранены.
Для восстановления включите MySQL (`systemctl enable --now mysql`),
отключите `mpm_event`, включите `mpm_prefork` и `php8.1` через a2dismod/a2enmod,
выполните `a2dissite pitchai-stopped`, `a2ensite 000-default 000-default-le-ssl`,
затем `apache2ctl configtest` и `systemctl restart apache2`.
Goulash продолжает использовать отдельный виртуальный хост `goulash.conf`.

# Альтернативное размещение на Amvera

В `amvera.yml` настроены сборка существующего `Dockerfile`, HTTP-порт 8000
и постоянное хранилище `/data`. SQLite и сессии посетителей сохраняются
между перезапусками. Локальные базы и токены не входят в Docker-образ.

Создайте приложение в [Amvera](https://cloud.amvera.ru), выберите окружение
Docker и тариф с памятью от 500 МБ. Загрузите исходники из ветки
`feature/supplier-search`, включая `amvera.yml` и `Dockerfile`.

После назначения публичного HTTPS-домена задайте переменную окружения
`GOULASH_PUBLIC_ORIGIN=https://<домен приложения>` без завершающего слеша
и перезапустите приложение. Это нужно для проверки Origin при сохранении
данных. `GOULASH_PUBLIC_DEMO=1` и `GOULASH_DB=/data/goulash.db` уже заданы
в Dockerfile; сессии автоматически сохраняются в `/data/sessions`.

Проверьте `GET /api/meta`, поиск, сохранение подборки, сравнение и изоляцию
данных в двух разных браузерных сессиях. Конфигурация в репозитории сама
по себе не создаёт приложение и не запускает оплату.

# Альтернативное размещение на Render

В `render.yaml` описан один Docker web service в регионе Frankfurt:
`0.5c-512mb`, один экземпляр, постоянный диск 1 ГБ в `/data` для SQLite
и сессий посетителей. Проверка здоровья: `GET /api/meta`.

[Создать сервис из Blueprint](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FVovaArrow%2Fgoulash_test%2Ftree%2Ffeature%2Fsupplier-search).

Сервер и диск платные. Точную стоимость Render показывает перед созданием.
Для доступа к диску не выбирайте бесплатный compute-план.

Контейнер слушает порт 8000; `PORT=8000` задан в Blueprint.
`GOULASH_PUBLIC_DEMO=1` разделяет данные разных посетителей.
Публичный Origin автоматически берётся из `RENDER_EXTERNAL_URL`.
Если используется собственный домен, задайте `GOULASH_PUBLIC_ORIGIN` явно.

В сервис загружаются код из GitHub и статические ресурсы. Локальные базы,
токены и сессии с компьютера в Docker-образ не включаются.

После успешного deploy нужно проверить публичную HTTPS-ссылку: поиск,
«Мои подборки», «Реальный пример», сохранение ответа, сравнение и изоляцию
сессий. Создание файла Blueprint само по себе не публикует приложение.
