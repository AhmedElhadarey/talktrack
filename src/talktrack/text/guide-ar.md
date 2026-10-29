
## Arabic narration

This walkthrough has an Egyptian Arabic voice. Write the narration in Egyptian Arabic the way a Cairene explains at a whiteboard (ده، بتاع، علشان), not Modern Standard Arabic. Slide text may be Arabic or English; the player lays Arabic out right to left.

- **Plain text.** Send the words without harakat. Marking whole sentences makes the voice less clear.
- **Mark only what can be misread.** Give harakat to a single word whose meaning changes with its vowels, in a say override: `[بتتعرض]{say="بِتِتْعِرِض"}` (is shown, not بِتِتْعَرَّض, is exposed to). The usual cases: words that look alike without vowels (مهمة: مَهَمَّة a task, مُهِمَّة important), active and passive or participle forms (مُحَرِّر an editor, مُحَرَّر edited), and a doubled letter that changes the word (شَغَّل ran, شُغْل work). The reply names words it knows are ambiguous (`arabic_reading`); mark the meaning you intend, then revise the slide.
- **Numbers and symbols as words.** Write ٢٥ جيجا as خمسة وعشرين جيجا, 3:30 as تلاتة ونص, 0.1.8 as زيرو نقطة واحد نقطة تمانية.
- **English terms stay English.** Write talktrack, MLX, `clear_cache` in Latin letters; the voice reads them in English. Code in backticks is read as words.
- **When the user says a word sounds wrong,** call `add_pronunciation` with the word and the harakat they want, and the walkthrough's handle, so it is fixed here and in every walkthrough after.
- **Speed** does not change an Arabic voice.
