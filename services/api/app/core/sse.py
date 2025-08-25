def sse_iter(gen):
    for chunk in gen:
        if not chunk:
            continue
        yield f"data: {chunk}\n\n"
