# How this action works
> Voice a walkthrough on the runner, then post it to the pull request

This action turns a Markdown walkthrough into a narrated page, without sending anything to a service.

## Voice it once {#voice .side}
> One folder export voices the Markdown; the page and the video reuse it

```yaml file=action.yml lines=71-79
```

[The folder export](#L73 "showntell export") voices every paragraph on the runner, with the open voice model cached between runs.

The page and [the video](#L76 "walkthrough.mp4") are drawn from that same walkthrough, so nothing is voiced twice.

## One comment per pull request {#comment}
> Each push updates the same comment

```yaml file=action.yml lines=104-114
```

The comment starts with [a hidden marker](#L104 "marker="). On the next push, the action [finds that comment](#L110 "existing=") and edits it, so the thread doesn't fill with copies.
