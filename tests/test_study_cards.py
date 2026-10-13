from __future__ import annotations

import unittest

from konspekt.study_cards import extract_study_cards, find_hint

LESSON = """# Структуры данных

## Стек

Стек работает по принципу LIFO [00:12:34]. Операции push и pop выполняются за O(1).

## Очередь

Очередь работает по принципу FIFO [00:20:00]. Её реализуют на кольцевом буфере.

## Ключевые понятия

- **Стек** — структура данных LIFO.
- **Очередь (queue)** — структура данных FIFO.
  - пример: очередь печати

## Мини-словарь

- **Стек** — структура данных, где последний добавленный элемент извлекается первым.
- Кольцевой буфер — массив, замкнутый в кольцо.
- **Амортизация:** средняя стоимость операции.

| Термин | Определение | Пример |
|---|---|---|
| Хэш-таблица | Структура с доступом по ключу | dict |

## Вопросы для самопроверки

1. Что такое стек?
   Ответ: структура данных LIFO [00:12:34].
2. **Как работает очередь?**
   **Ответ:** по принципу FIFO.
3. Где применяют кольцевой буфер?
4. Какая сложность у push? — Ответ: O(1).
5. Чем дек отличается от очереди?

## Ответы

5. Дек позволяет добавлять и удалять с обоих концов.

## План повторения

- через день перечитать
"""


class ExtractStudyCardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cards = extract_study_cards(LESSON)
        self.by_front = {card.front: card for card in self.cards}

    def test_questions_with_answers_become_qa_cards(self) -> None:
        card = self.by_front["Что такое стек?"]
        self.assertEqual((card.kind, card.back), ("qa", "структура данных LIFO [00:12:34]."))
        self.assertEqual(card.section, "Вопросы для самопроверки")
        self.assertEqual(card.timestamps, (754,))
        self.assertFalse(card.hint)

        self.assertEqual(self.by_front["Как работает очередь?"].back, "по принципу FIFO.")
        self.assertEqual(self.by_front["Какая сложность у push?"].back, "O(1).")

    def test_answers_from_a_separate_section_are_matched_by_number(self) -> None:
        card = self.by_front["Чем дек отличается от очереди?"]
        self.assertEqual(card.kind, "qa")
        self.assertEqual(card.back, "Дек позволяет добавлять и удалять с обоих концов.")

    def test_question_without_answer_gets_a_hint_from_the_lesson(self) -> None:
        card = self.by_front["Где применяют кольцевой буфер?"]

        self.assertEqual(card.kind, "question")
        self.assertTrue(card.hint)
        self.assertIn("кольцевом буфере", card.back)
        self.assertEqual(card.timestamps, (1200,))

    def test_glossary_terms_from_lists_and_tables(self) -> None:
        self.assertEqual(
            self.by_front["Кольцевой буфер"].back,
            "массив, замкнутый в кольцо.",
        )
        self.assertEqual(self.by_front["Амортизация"].back, "средняя стоимость операции.")
        self.assertEqual(
            self.by_front["Хэш-таблица"].back,
            "Структура с доступом по ключу\n**Пример:** dict",
        )
        queue = self.by_front["Очередь (queue)"]
        self.assertEqual(queue.back, "структура данных FIFO.\n- пример: очередь печати")
        self.assertEqual(queue.timestamps, (1200,))
        self.assertTrue(all(card.kind == "term" for card in self.cards[:5]))

    def test_duplicate_terms_keep_the_fuller_definition(self) -> None:
        stacks = [card for card in self.cards if card.front == "Стек"]

        self.assertEqual(len(stacks), 1)
        self.assertIn("последний добавленный", stacks[0].back)

    def test_ids_are_stable_and_ignore_formatting(self) -> None:
        again = extract_study_cards(LESSON.replace("1. Что такое стек?", "1. **Что такое  стек**"))
        ids = {card.card_id for card in self.cards}

        self.assertEqual(len(ids), len(self.cards))
        self.assertIn(
            next(card for card in again if card.front.startswith("Что такое")).card_id, ids
        )

    def test_review_plan_and_other_sections_do_not_produce_cards(self) -> None:
        self.assertNotIn("через день перечитать", self.by_front)
        self.assertEqual(len(self.cards), 10)

    def test_lesson_without_card_sections(self) -> None:
        self.assertEqual(extract_study_cards("# Тема\n\nПросто текст."), [])
        self.assertEqual(extract_study_cards(""), [])

    def test_other_question_layouts(self) -> None:
        cards = extract_study_cards(
            "## Самопроверка\n\n"
            "- Что такое граф?\n\n  **Ответ**: набор вершин и рёбер.\n"
            "- Что такое дерево?\n- Ответ — связный граф без циклов.\n\n"
            "```text\n1. Не вопрос\n```\n"
        )

        self.assertEqual(
            [(card.front, card.back) for card in cards],
            [
                ("Что такое граф?", "набор вершин и рёбер."),
                # Code after an answer is part of that answer, never a new question.
                ("Что такое дерево?", "связный граф без циклов.\n```text\n1. Не вопрос\n```"),
            ],
        )


class FindHintTests(unittest.TestCase):
    def test_returns_best_passage_or_nothing(self) -> None:
        self.assertIn("LIFO", find_hint(LESSON, "Как работают push и pop?"))
        self.assertEqual(find_hint(LESSON, "Квантовая хромодинамика?"), "")


if __name__ == "__main__":
    unittest.main()
