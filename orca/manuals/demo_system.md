# Demo System Manual

## Overview
This is a demo system that processes data transformation tasks.

## API
- `transform(input, format)` → converts input to the target format
- `validate(data)` → validates data structure, returns True/False
- `summarize(text, max_words)` → summarizes text to max_words

## Rules
- Always validate before transforming
- Use format "json" for structured data, "text" for human-readable
- Summaries should be at most 50 words unless specified

## Examples

Task: "Summarize this: The quick brown fox jumps over the lazy dog."
Result: "A fox jumps over a dog."

Task: "Transform the list [1,2,3] to JSON"
Result: {"items": [1, 2, 3], "count": 3}
