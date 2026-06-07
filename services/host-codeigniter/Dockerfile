FROM php:8.3-cli-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git unzip libpq-dev \
    && docker-php-ext-install pdo_pgsql \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /app/services/host-codeigniter

COPY services/host-codeigniter/composer.json ./
RUN composer install --no-dev --prefer-dist --no-interaction --no-progress

COPY services/host-codeigniter/ ./
COPY database/ /app/database/

RUN mkdir -p writable/cache writable/logs writable/session writable/uploads

EXPOSE 8088 2575

CMD ["sh", "-c", "php spark serve --host 0.0.0.0 --port ${WEB_PORT:-8088} & exec php spark hl7:mllp-listen"]
