import { useRef, useState, type FormEvent } from 'react'

import { ApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { useParsePhrase, type OperationDraft } from './queries'

// Столько принимает бэк: фраза уходит модели, а каждый символ — это токены
const PHRASE_MAX_LENGTH = 200

interface PhraseInputProps {
  value: string
  onChange: (value: string) => void
  /** Модель разобрала фразу: страница откроет окно записи с черновиком. */
  onDraft: (draft: OperationDraft, input: HTMLInputElement) => void
}

/** Запись словами: «кофе 350 вчера» → черновик в окне записи. */
export function PhraseInput({ value, onChange, onDraft }: PhraseInputProps) {
  const parse = useParsePhrase()
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const text = value.trim()

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!text || parse.isPending) return
    setError(null)
    parse.mutate(text, {
      onSuccess: (draft) => {
        if (inputRef.current) onDraft(draft, inputRef.current)
      },
      onError: (failure) => setError(errorMessage(failure)),
    })
  }

  return (
    <form onSubmit={handleSubmit}>
      <label htmlFor="phrase" className="sr-only">
        Запись словами
      </label>
      <div className="flex gap-2">
        <Input
          ref={inputRef}
          id="phrase"
          value={value}
          onChange={(event) => {
            onChange(event.target.value)
            // Ошибка относилась к прошлой фразе: новая её не заслуживает
            setError(null)
          }}
          placeholder="Например: кофе 350 вчера"
          maxLength={PHRASE_MAX_LENGTH}
          autoComplete="off"
          enterKeyHint="go"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'phrase-error' : undefined}
          className="h-11 bg-sheet"
        />
        {/* Недоступна, пока идёт разбор: повторное нажатие не отправит фразу второй раз */}
        <Button type="submit" size="lg" className="h-11 px-5" disabled={!text || parse.isPending}>
          {parse.isPending ? 'Разбираем…' : 'Разобрать'}
        </Button>
      </div>
      {/* Контейнер на месте всегда: скринридер объявит ошибку, когда она появится */}
      <div role="alert">
        {error && (
          <p id="phrase-error" className="mt-2 text-sm text-expense">
            {error}
          </p>
        )}
      </div>
    </form>
  )
}

function errorMessage(error: unknown): string {
  // Бэк объясняет отказ по-человечески: фразу не поняли, нет категорий, модель недоступна
  if (error instanceof ApiError && error.detail) return error.detail
  return 'Не удалось разобрать фразу. Попробуйте ещё раз или выберите категорию плиткой.'
}
