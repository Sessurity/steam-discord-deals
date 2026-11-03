# ZHAXO — прямі Discord-команди через Cloudflare Workers

Це новий HTTP-обробник slash-команд. Він **не залежить від GitHub Actions schedule**: Discord передає виклик безпосередньо Cloudflare Worker. Код уже може перебувати у GitHub, але **поки його не розгорнули в Cloudflare і не вказали endpoint у Discord, команди не працюватимуть**.

Підтримуються /help, /deals, /deals discount:90, /spore, /find game:назва. Показує до 30 популярніших ігор із знижками 70%+, відсортованих за відсотком. Обкладинки великі, ціни Steam UA в гривнях, Steam RU в рублях (саме регіональні, не конвертація). SPORE окремо.

## Разове налаштування

1. Відкрий https://dash.cloudflare.com → Workers & Pages → Create Worker. Завантаж/розгорни код worker/src/index.js. Якщо підключаєш GitHub, конфігурація розміщена у worker/wrangler.toml. Для ручного редагування створи Worker, вибери Edit code і встав вміст файлу, потім Deploy.
2. Відкрий https://discord.com/developers/applications, вибери ZHAXO, у General Information скопіюй Public Key. У Cloudflare Worker Settings → Variables and Secrets створи DISCORD_PUBLIC_KEY зі значенням цього ключа. Це публічний ключ перевірки підписів, не токен бота.
3. Створи у Worker змінну DISCORD_CHANNEL_ID з числовим ID каналу #deals (в Discord увімкнути Developer Mode → Copy Channel ID). Опційно додай DISCORD_GUILD_ID з ID сервера та DISCORD_ALLOWED_USER_IDS (через кому), якщо хочеш обмежити доступ.
4. У Discord Developer Portal → General Information → Interactions Endpoint URL встав HTTPS-адресу Worker з доданим /discord, наприклад https://zhaxo-steam-commands.example.workers.dev/discord. Натисни Save Changes. Discord перевірить підпис та надішле PING.
5. У GitHub Settings → Secrets and variables → Actions створи DISCORD_APP_ID (Application ID зі сторінки General Information) та DISCORD_GUILD_ID (ID твого Discord-сервера). DISCORD_BOT_TOKEN уже використовується для ранкових звітів ZHAXO.
6. Відкрий GitHub Actions → Register ZHAXO slash commands → Run workflow (main). Цей ручний workflow реєструє /deals, /spore, /find, /help у вибраному Discord-сервері.
7. Спробуй /help у #deals, а потім /spore та /deals. Discord підтверджує виклик без очікування GitHub; фактичне отримання всіх ігор Steam може тривати довше.

## Що залишилося

Старі повідомлення з префіксом !, зокрема !deals, все ще обробляє попередній скрипт discord_commands.py через GitHub Actions. Cloudflare slash-команди використовують / замість !. Поки нову систему не протестували в Discord, старий обробник залишено увімкненим, щоб не втратити наявні функції.

Після успішного налаштування можна вимкнути лише workflow Discord Steam Commands. Workflow Steam discounts → Discord, який надсилає ранкові добірки, **залишити увімкненим**.

## Обмеження

Потрібен Cloudflare Worker у твоєму акаунті. Cloudflare Free має обмеження CPU та HTTP-запитів; Steam іноді не відповідає або блокує частину запитів. Discord потребує відповіді на взаємодію за 3 секунди, тому Worker відразу підтверджує команду, а потім редагує повідомлення. Подальша робота може завершитися помилкою при перевищенні дозволеного часу. Локальні тести не замінюють реального тестування після розгортання. Нікому не надсилай Discord Bot Token.
