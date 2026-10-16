showntell turns Markdown into narrated slide walkthroughs, voiced locally and played in the browser.

Use it when watching and listening would teach faster than reading: code walkthroughs, diffs and pull requests, concepts, architecture and data flow, maths.

Flow: start_walkthrough → add_slides with only the title slide and one or two spoken sentences → share the link → add_slides, one to three slides per call → finish_walkthrough.

The first add_slides reply says when the link is playable. Send that link to the user at once, in a message, before your next tool call; they watch while you write the rest. That reply also carries the full authoring guide; read it before writing the body.

To change a walkthrough later, find its handle with list_walkthroughs, read its Markdown from the resource showntell://walkthroughs/{id}, and replace slides with revise_slide.
