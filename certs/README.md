# Сертификаты

`russian_trusted_root_ca_pem.crt` — корневой сертификат Минцифры «Russian Trusted Root CA». Им подписаны серверы GigaChat, а в наборе доверенных сертификатов, с которым работает Python, его нет: без этого файла запросы к GigaChat падают с `CERTIFICATE_VERIFY_FAILED`.

Сертификат публичный, секретов в нём нет. Клиент GigaChat доверяет только ему (`GIGACHAT_CA_BUNDLE_FILE`), проверка TLS не отключается.

- Источник: https://www.gosuslugi.ru/crt, файл https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
- Действует до 27.02.2032
- Отпечаток SHA-256: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`

Сверить отпечаток:

```bash
openssl x509 -in certs/russian_trusted_root_ca_pem.crt -noout -fingerprint -sha256
```
