def square_name(row: int, col: int) -> str:
    """Column letters then row number, like H8."""
    letters = ""
    n = col
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return f"{letters}{row + 1}"
