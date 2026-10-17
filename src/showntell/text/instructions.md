showntell turns Markdown into narrated slide walkthroughs, voiced locally and played in the browser.

Use it when watching and listening would teach faster than reading: code walkthroughs, diffs and pull requests, concepts, architecture and data flow, maths.

Flow: start_walkthrough with `opening`, the title slide and one or two spoken sentences → share the link → add_slides, one to three slides per call → finish_walkthrough, whose reply lists render and layout problems found by drawing every slide → revise_slide what it names. Call preview_slides only for a slide it names, a drawing or diagram you want to see, or when the user asks.

Pass `project_dir`, the project's absolute path, to start_walkthrough; a client that does not share its project folder otherwise has code read from the server's working directory.

The reply that carries the opening says when the link is playable: the start_walkthrough reply, or the first add_slides reply if you started without `opening`. Send that link to the user at once, in a message, before your next tool call; they watch while you write the rest. That reply also carries the full authoring guide; read it before writing the body.

To change a walkthrough later, find its handle with list_walkthroughs, read its Markdown from the resource showntell://walkthroughs/{id}, and replace slides with revise_slide.
