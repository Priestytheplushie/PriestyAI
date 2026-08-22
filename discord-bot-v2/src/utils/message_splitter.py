from typing import List

class MessageSplitter:

    @staticmethod
    def split(text: str, max_length: int = 1900) -> List[str]:
        if len(text) <= max_length:
            return [text]

        chunks: List[str] = []
        current_chunk = ""
        in_code_block = False
        code_block_lang = ""

        lines = text.splitlines(keepends=True)

        for line in lines:
            if line.strip().startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_block_lang = line.strip()[3:].strip()
                else:
                    in_code_block = False
                    code_block_lang = ""

            if len(current_chunk) + len(line) > max_length:
                if current_chunk:
                    if in_code_block:
                        current_chunk += "\n```"
                        chunks.append(current_chunk)
                        current_chunk = f"```{code_block_lang}\n" + line
                    else:
                        chunks.append(current_chunk)
                        current_chunk = line
                else:
                    while len(line) > max_length:
                        chunks.append(line[:max_length])
                        line = line[max_length:]
                    current_chunk = line
            else:
                current_chunk += line

        if current_chunk:
            chunks.append(current_chunk)

        return chunks