import { expect, it } from 'vitest'

import { detailOf } from './errors'

it('detailOf отдаёт пояснение бэка, только если это текст', () => {
  expect(detailOf({ detail: 'Не получилось понять запись' })).toBe('Не получилось понять запись')
  // Ошибка валидации FastAPI: detail — список полей, человеку его не показать
  expect(detailOf({ detail: [{ loc: ['body', 'text'], msg: 'too long' }] })).toBeUndefined()
  expect(detailOf({ message: 'другой формат' })).toBeUndefined()
  expect(detailOf(undefined)).toBeUndefined()
  expect(detailOf('строка')).toBeUndefined()
})
