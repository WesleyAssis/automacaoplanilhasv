import pdfplumber
import pandas as pd
import re
import sys

PDF_PATH = "02 - PDF - Fatores de Atuallizacao Monetaria - Dezembro de 2025.pdf"
CSV_SAIDA = "ICGJ.csv"

MESES_ORDEM = [
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez"
]

PALAVRAS_OBRIGATORIAS = [
    "Fatores de Atualização Monetária",
    "ICGJ",
    "TJMG"
]

def extrair_texto_pdf(caminho):
    texto = ""
    with pdfplumber.open(caminho) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                texto += "\n" + page_text
    return texto

def validar_pdf(texto):
    texto_upper = texto.upper()

    for palavra in PALAVRAS_OBRIGATORIAS:
        if palavra.upper() not in texto_upper:
            raise ValueError(
                "ERRO: O PDF informado não corresponde à tabela ICGJ do TJMG."
            )

def parse_tabela(texto):
    linhas = texto.splitlines()
    registros = []

    for linha in linhas:
        if re.match(r"^\s*(19|20)\d{2}\s", linha):
            ano = linha.split()[0]

            valores = re.findall(r"\d+,\d+", linha)
            valores = valores[:12]

            if len(valores) < 12:
                continue  # linha incompleta, ignora

            for i, valor in enumerate(valores):
                indice = float(valor.replace(",", "."))
                mes = MESES_ORDEM[i]

                registros.append({
                    "Referencia": f"{mes}/{ano}",
                    "Referencia_yyyymm": f"{ano}{str(i+1).zfill(2)}",
                    "Indice": indice
                })

    if not registros:
        raise ValueError(
            "ERRO: Nenhum dado numérico válido foi encontrado no PDF."
        )

    return pd.DataFrame(registros)

def main():
    try:
        texto = extrair_texto_pdf(PDF_PATH)

        if not texto.strip():
            raise ValueError("ERRO: O PDF está vazio ou não possui texto legível.")

        validar_pdf(texto)

        df = parse_tabela(texto)

        df = df.sort_values("Referencia_yyyymm").reset_index(drop=True)

        df.to_csv(CSV_SAIDA, index=False, encoding="utf-8-sig")

        print(f"✔ Arquivo gerado com sucesso: {CSV_SAIDA}")
        print(f"✔ Total de registros: {len(df)}")

    except Exception as e:
        print(str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
