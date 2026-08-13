# Beta38: world datapacks и локализация Modonomicon

## Цель

Beta38 не записывает переводы в `minecraft/kubejs/data` и не использует
OpenLoader как глобальную точку установки. Все `data/...` результаты сначала
собираются в отдельный проверяемый ZIP-datapack, после чего его идентичная копия
атомарно устанавливается во все существующие миры из `minecraft/saves`, имеющие
`level.dat`.

## Modonomicon

Literal-текст в Modonomicon нельзя заменить обычным resource pack: он хранится
в серверной структуре `data/...`. Для такого текста Beta38 создаёт стабильный
DescriptionId из namespace, book id, относительного пути JSON и пути поля
внутри документа. Например:

`mineai.book.paganbless.pagan_guide.entries.features.herbalist_bench.pages.page_0.text`

Datapack содержит исходную структуру книги, но literal-поля заменены этими
ключами. Resource pack получает две карты:

- `assets/<namespace>/lang/en_us.json` — исходный английский текст;
- `assets/<namespace>/lang/<target>.json` — проверенный перевод.

Уже существующие DescriptionId остаются без изменений и используют обычный
механизм companion-lang Beta37.

## Безопасность

- Каталоги `kubejs/data` и `config/openloader` не являются целями записи.
- Мир определяется только как прямой подкаталог `saves` с файлом `level.dat`.
- Во все миры записываются одинаковые байты уже проверенного ZIP.
- При ошибке установки восстановляются прежние архивы во всех затронутых мирах.
- Если миров нет, master-архив сохраняется в `MineAI_Datapacks` для ручной
  установки; новый мир, созданный позже, требует повторного запуска установки.
