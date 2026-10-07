/** Неуспешный ответ API: по статусу страница выбирает понятное сообщение. */
export class ApiError extends Error {
  readonly status: number
  /** Пояснение бэка для человека, если он прислал его строкой. */
  readonly detail?: string

  constructor(status: number, detail?: string) {
    super(detail ?? `Запрос завершился с кодом ${status}`)
    this.status = status
    this.detail = detail
  }
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

/** 409: запись конфликтует с существующей, например имя категории уже занято. */
export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409
}

/** Бросает ApiError, если ответ неуспешный: для мутаций, где тело ответа не нужно. */
export function ensureOk(response: Response): void {
  if (!response.ok) throw new ApiError(response.status)
}

/**
 * detail из тела ошибки FastAPI, если это текст для человека. У ошибок
 * валидации detail — список полей: такой показывать нельзя.
 */
export function detailOf(body: unknown): string | undefined {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    return typeof body.detail === 'string' ? body.detail : undefined
  }
  return undefined
}
