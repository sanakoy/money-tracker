import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import type { CategoryDialogState } from '@/categories/category-dialog'
import { CategoryShares } from '@/categories/category-shares'
import { useCategories, type Category, type OperationKind } from '@/categories/queries'
import { KindToggle } from '@/components/kind-toggle'
import { formatAmount } from '@/lib/format'
import {
  formatMonthParam,
  isSameMonth,
  monthName,
  monthOfIsoDate,
  parseMonthParam,
  type YearMonth,
} from '@/lib/month'
import { CategoryTiles, type TileStamp } from '@/operations/category-tiles'
import { MonthSummary } from '@/operations/month-summary'
import { MonthSwitcher } from '@/operations/month-switcher'
import { NewOperationDialog, type OperationDraftValues } from '@/operations/new-operation-dialog'
import { PhraseInput } from '@/operations/phrase-input'
import { useMonthOperations, type OperationDraft } from '@/operations/queries'
import { summarize } from '@/operations/summary'

// Окно категории нужно редко, а тянет за собой выбор иконки (Popover и
// позиционирование): его код скачивается при первом открытии, а не с главной страницей
const loadCategoryDialog = () => import('@/categories/category-dialog')
const CategoryDialog = lazy(async () => ({ default: (await loadCategoryDialog()).CategoryDialog }))

// Столько же длится анимация .stamp-flash в index.css
const STAMP_VISIBLE_MS = 2000
const KIND_WORD: Record<OperationKind, string> = { spending: 'расход', profit: 'доход' }
const EMPTY_CATEGORIES: Record<OperationKind, string> = {
  spending: 'Категорий расходов пока нет.',
  profit: 'Категорий доходов пока нет.',
}

export function LedgerPage() {
  // Месяц живёт в адресе (?month=2026-09): работает «Назад», ссылку можно сохранить
  const [searchParams, setSearchParams] = useSearchParams()
  const month = parseMonthParam(searchParams.get('month'))
  const operations = useMonthOperations(month)

  const [kind, setKind] = useState<OperationKind>('spending')
  const categories = useCategories(kind, month)
  // Сразу после переключения «Расход / Доход» кеш ещё отдаёт категории
  // прежнего типа (placeholderData): такие плитки показывать нельзя
  const tiles = categories.data?.filter((category) => category.is_profit === (kind === 'profit'))

  // Категория, в которую сейчас пишем; null — окно закрыто
  const [selected, setSelected] = useState<Category | null>(null)
  // Окно открыто по фразе: значения, которые предложила модель
  const [draft, setDraft] = useState<OperationDraftValues | null>(null)
  const [phrase, setPhrase] = useState('')
  const openerRef = useRef<HTMLElement | null>(null)
  const [stamp, setStamp] = useState<TileStamp | null>(null)
  // То же, что штамп, но для скринридера: штамп он не видит
  const [announcement, setAnnouncement] = useState('')
  // Окно категории: новая (с «+») или изменение (из окна записи); null — закрыто
  const [categoryDialog, setCategoryDialog] = useState<CategoryDialogState | null>(null)
  // Окно категории монтируется при первом открытии и дальше остаётся в дереве:
  // иначе при закрытии оно исчезало бы без анимации и без возврата фокуса
  const [categoryDialogUsed, setCategoryDialogUsed] = useState(false)
  function openCategoryDialog(state: CategoryDialogState) {
    setCategoryDialogUsed(true)
    setCategoryDialog(state)
  }
  // Категория, которую попросили изменить из окна записи: её окно откроется,
  // когда окно записи закроется совсем
  const pendingCategoryEdit = useRef<Category | null>(null)

  // Код окна категории качаем заранее, сразу после показа главной: в основной
  // бандл он не попадает, но к первому нажатию «+» уже готов, без задержки
  useEffect(() => {
    void loadCategoryDialog()
  }, [])

  useEffect(() => {
    if (stamp === null) return
    const timer = setTimeout(() => setStamp(null), STAMP_VISIBLE_MS)
    return () => clearTimeout(timer)
  }, [stamp])

  function showMonth(next: YearMonth) {
    setSearchParams({ month: formatMonthParam(next) })
  }

  function openDraft(parsed: OperationDraft, input: HTMLInputElement) {
    openerRef.current = input
    // Плитки за окном — того же типа, что категория черновика: после записи
    // штамп встанет на её плитку, а не останется невидимым на другой вкладке
    setKind(parsed.category.is_profit ? 'profit' : 'spending')
    setDraft({ sum: parsed.sum, date: parsed.date, comment: parsed.comment })
    setSelected(parsed.category)
  }

  function handleSaved(category: Category, amount: number, isoDate: string) {
    // Фраза записана — поле свободно для следующей. Если окно закрыли без
    // записи, фраза остаётся: её можно поправить и разобрать снова
    if (draft) setPhrase('')
    setStamp({ categoryId: category.id, key: Date.now() })
    setAnnouncement(`Записано: ${formatAmount(amount)} в «${category.name}».`)
    // Запись в другой месяц иначе «пропала» бы: показываем месяц, куда она легла
    const savedMonth = monthOfIsoDate(isoDate)
    if (!isSameMonth(savedMonth, month)) showMonth(savedMonth)
  }

  return (
    <main className="mt-8">
      <MonthSwitcher month={month} onChange={showMonth} />

      <div className="mt-6">
        {operations.data ? (
          <MonthSummary
            netLabel={`Итог за ${monthName(month)}`}
            summary={summarize(operations.data)}
          />
        ) : (
          // Место под итоги занято заранее, чтобы плитки не прыгали после загрузки
          <div className="h-[8.5rem] border-y-[3px] border-double border-ink sm:h-[6.25rem]" />
        )}
      </div>

      <section aria-labelledby="new-entry-title" className="mt-10">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="new-entry-title" className="text-lg font-semibold">
            Новая запись
          </h2>
          <KindToggle name="ledger-kind" value={kind} onChange={setKind} />
        </div>
        <div className="mt-3">
          <PhraseInput value={phrase} onChange={setPhrase} onDraft={openDraft} />
        </div>
        <p id="tiles-hint" className="mt-3 text-sm text-muted-foreground">
          {tiles?.length === 0
            ? `${EMPTY_CATEGORIES[kind]} Добавьте первую плиткой «+».`
            : `Или нажмите на категорию, чтобы записать ${KIND_WORD[kind]}.`}
        </p>

        <div className="mt-4">
          {categories.isError ? (
            <p className="text-sm text-expense">Не удалось загрузить категории.</p>
          ) : !tiles ? (
            <p className="text-sm text-muted-foreground">Загрузка категорий…</p>
          ) : (
            <CategoryTiles
              categories={tiles}
              hintId="tiles-hint"
              stamp={stamp}
              onSelect={(category, tile) => {
                openerRef.current = tile
                setDraft(null)
                setSelected(category)
              }}
              onCreate={(tile) => {
                openerRef.current = tile
                openCategoryDialog({ mode: 'create', kind })
              }}
            />
          )}
        </div>
        <p role="status" className="sr-only">
          {announcement}
        </p>
      </section>

      <div className="mt-12">
        <CategoryShares kind={kind} month={month} />
      </div>

      {/* Список записей живёт в статистике: туда и ведём, с тем же месяцем */}
      <p className="mt-6 text-sm">
        <Link
          to={`/statistics?month=${formatMonthParam(month)}`}
          className="text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          Все записи за {monthName(month)}
        </Link>
      </p>

      <NewOperationDialog
        category={selected}
        draft={draft}
        returnFocusTo={openerRef}
        onClose={() => setSelected(null)}
        onSaved={handleSaved}
        onEditCategory={(category) => {
          pendingCategoryEdit.current = category
          setSelected(null)
        }}
        onAfterClose={(event) => {
          const category = pendingCategoryEdit.current
          if (!category) return
          pendingCategoryEdit.current = null
          event.preventDefault()
          openCategoryDialog({ mode: 'edit', category })
        }}
      />

      <Suspense fallback={null}>
        {categoryDialogUsed && (
          <CategoryDialog
            state={categoryDialog}
            siblings={tiles ?? []}
            returnFocusTo={openerRef}
            onClose={() => setCategoryDialog(null)}
            onDone={setAnnouncement}
          />
        )}
      </Suspense>
    </main>
  )
}
