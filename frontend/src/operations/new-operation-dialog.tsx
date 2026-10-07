import { useState, type FormEvent, type RefObject } from 'react'

import { isNotFound } from '@/api/errors'
import { CategoryIcon } from '@/categories/icons'
import type { Category } from '@/categories/queries'
import { FormField } from '@/components/form-field'
import { KIND_LABELS } from '@/components/kind-toggle'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { todayIsoDate } from '@/lib/month'

import { useCreateOperation } from './queries'
import {
  amountToInput,
  COMMENT_MAX_LENGTH,
  validateOperationFields,
  type OperationFieldErrors,
} from './validation'

/** Что предложила модель по фразе: окно откроется уже заполненным. */
export interface OperationDraftValues {
  /** null — суммы во фразе не было, её впишет человек. */
  sum: number | null
  /** «2026-10-07» */
  date: string
  comment: string | null
}

interface NewOperationDialogProps {
  /** Категория, в которую пишем; null — окно закрыто. */
  category: Category | null
  /** Черновик из фразы. Без него — пустая сумма и сегодняшняя дата. */
  draft?: OperationDraftValues | null
  /** Плитка, с которой открыли окно: после закрытия фокус вернётся на неё. */
  returnFocusTo: RefObject<HTMLElement | null>
  onClose: () => void
  /** Запись сохранена: страница поставит штамп и покажет месяц этой даты. */
  onSaved: (category: Category, amount: number, isoDate: string) => void
  /** «Изменить категорию»: страница закроет это окно и откроет окно категории. */
  onEditCategory: (category: Category) => void
  /**
   * Окно полностью закрылось. Если страница хочет сразу открыть другое окно,
   * она отменяет событие — тогда фокус на плитку не возвращается и не
   * отнимается у нового окна.
   */
  onAfterClose?: (event: Event) => void
}

export function NewOperationDialog({
  category,
  draft,
  returnFocusTo,
  onClose,
  onSaved,
  onEditCategory,
  onAfterClose,
}: NewOperationDialogProps) {
  return (
    <Dialog open={category !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="gap-0 bg-paper p-6 sm:max-w-md"
        // Radix вернул бы фокус на DialogTrigger, а окно открыто плиткой без него
        onCloseAutoFocus={(event) => {
          onAfterClose?.(event)
          if (event.defaultPrevented) return
          if (returnFocusTo.current?.isConnected) {
            event.preventDefault()
            returnFocusTo.current.focus()
          }
        }}
      >
        {/* key: у каждой категории своё состояние формы, от прошлой ничего не остаётся */}
        {category && (
          <NewOperationForm
            key={category.id}
            category={category}
            draft={draft ?? null}
            onClose={onClose}
            onSaved={onSaved}
            onEditCategory={onEditCategory}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function NewOperationForm({
  category,
  draft,
  onClose,
  onSaved,
  onEditCategory,
}: Pick<NewOperationDialogProps, 'onClose' | 'onSaved' | 'onEditCategory'> & {
  category: Category
  draft: OperationDraftValues | null
}) {
  const [amount, setAmount] = useState(draft?.sum != null ? amountToInput(draft.sum) : '')
  const [date, setDate] = useState(() => draft?.date ?? todayIsoDate())
  const [comment, setComment] = useState(draft?.comment ?? '')
  const [errors, setErrors] = useState<OperationFieldErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const create = useCreateOperation()

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    const { sum, errors: nextErrors } = validateOperationFields(amount, date)
    setErrors(nextErrors)
    if (sum === null || !date) return

    create.mutate(
      {
        sum,
        category_id: category.id,
        // Бэк хранит дату со временем, а в интерфейсе важен только день
        date: `${date}T00:00:00`,
        comment: comment.trim() || null,
      },
      {
        onSuccess: () => {
          onClose()
          onSaved(category, sum, date)
        },
        onError: (error) =>
          setFormError(
            isNotFound(error)
              ? 'Категория не найдена: возможно, её удалили. Обновите страницу.'
              : 'Не удалось записать. Попробуйте ещё раз.',
          ),
      },
    )
  }

  const kindLabel = KIND_LABELS[category.is_profit ? 'profit' : 'spending']

  return (
    <form onSubmit={handleSubmit} noValidate>
      <DialogHeader className="flex-row items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full border border-rule bg-sheet">
          <CategoryIcon category={category} className="size-5" />
        </span>
        <div className="min-w-0 text-left">
          <DialogTitle className="text-lg">
            {kindLabel}: {category.name}
          </DialogTitle>
          <DialogDescription>
            {/* Черновик предложила модель: напоминаем, что его стоит проверить */}
            {draft ? 'Запись из фразы — проверьте её.' : 'Новая запись.'}{' '}
            {/* Управление категорией живёт здесь: плитка и так ведёт в её окно */}
            <button
              type="button"
              onClick={() => onEditCategory(category)}
              className="text-foreground underline underline-offset-4 hover:text-ink"
            >
              Изменить категорию
            </button>
          </DialogDescription>
        </div>
      </DialogHeader>

      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <FormField
          id="new-amount"
          label="Сумма, ₽"
          inputMode="decimal"
          autoComplete="off"
          placeholder="0,00"
          // Окно открыли ради суммы: курсор сразу в ней
          autoFocus
          className="amount h-11 bg-sheet text-lg"
          value={amount}
          error={errors.amount}
          onChange={(event) => setAmount(event.target.value)}
        />
        <FormField
          id="new-date"
          label="Дата"
          type="date"
          required
          value={date}
          error={errors.date}
          onChange={(event) => setDate(event.target.value)}
        />
        <div className="sm:col-span-2">
          <FormField
            id="new-comment"
            label="Комментарий"
            hint="Необязательно"
            maxLength={COMMENT_MAX_LENGTH}
            autoComplete="off"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
        </div>
      </div>

      <div role="alert" className="mt-3 min-h-5 text-sm text-expense">
        {formError}
      </div>

      <div className="mt-3 flex flex-col-reverse gap-2 border-t border-rule pt-4 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" size="lg" disabled={create.isPending} onClick={onClose}>
          Отмена
        </Button>
        {/* Кнопка недоступна, пока идёт запрос: повторный клик не отправит второй */}
        <Button type="submit" size="lg" className="px-8" disabled={create.isPending}>
          {create.isPending ? 'Записываем…' : 'Записать'}
        </Button>
      </div>
    </form>
  )
}
