import logging
from aiogram import Bot, Dispatcher, executor, types
import asyncio
import subprocess
import tempfile
import os
import pathlib
import wave
from vosk import Model, KaldiRecognizer
import json
import sqlite3
from settings import TELEGRAM_API_TOKEN, ADMIN_ID


dir_path = pathlib.Path.cwd()
# model_ru = Model(rf"{dir_path}/vosk-model-small-ru-0.22")
model_ru = Model(rf"{dir_path}/vosk-model-ru-0.22")  # download from https://alphacephei.com/vosk/models
print('Модель загружена')

# Объект бота
bot = Bot(token=TELEGRAM_API_TOKEN)
# Диспетчер для бота
dp = Dispatcher(bot)
# Включаем логирование, чтобы не пропустить важные сообщения
logging.basicConfig(level=logging.INFO)


def transcri(file, model):
    wf = wave.open(file, "rb")
    rec = KaldiRecognizer(model, 8000)

    result = ''
    last_n = False

    while True:
        data = wf.readframes(8000)
        if len(data) == 0:
            break

        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())

            if res['text'] != '':
                result += f" {res['text']}"
                last_n = False
            elif not last_n:
                result += '\n'
                last_n = True

    res = json.loads(rec.FinalResult())
    result += f" {res['text']}"

    print(result)
    if len(result) < 2:
        result = "Похоже, звуковое некорректное"
    return result


# лог в БД
def add_record_sql(id_user, datetime_add, date_add, text_message, username, userlastname, usermiddlename):
    with sqlite3.connect('main_db.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        sql = """
        INSERT INTO user_data (

                          id_user,
                          datetime_add,
                          date_add,
                          text_message,
                          username,
                          userlastname,
                          usermiddlename
                      )
                      VALUES (?, ?, ?, ?, ?, ?, ?);
        """

        cursor.execute(sql, (id_user, datetime_add, date_add, text_message, username, userlastname, usermiddlename))
        conn.commit()
        print("Запись успешно добавлена!")


# статистика админа
def get_statistics():
    '''Присылает кортеж с групированной статистикой по пользователю, колву транскрибаций и последней датой'''
    with sqlite3.connect('main_db.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        sql = """       
        SELECT id_user, username, count(*) as cnt_records, max(datetime_add) as last_time
        FROM user_data
        group by  id_user
        order by last_time desc
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        return rows


# конвертер голосового файла
def convert_to_pcm16b16000r(file_in):
    file_out = file_in.replace('telebot', 'converted')
    # Запрос в командную строку для обращения к FFmpeg
    command = [
        rf'{dir_path}/ffmpeg/bin/ffmpeg.exe',  # путь до ffmpeg.exe
        '-i', file_in,
        '-ac', '1',
        '-ab', '128',
        '-f', 'wav',

        '-ar', '8000',
        '-y', file_out
    ]
    print('команда', command)
    proc = subprocess.Popen(command, stderr=subprocess.DEVNULL)
    proc.wait()

    if file_in:
        os.remove(file_in)
    return file_out


@dp.message_handler(commands=['admin'])
async def admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        adm_message = get_statistics()
        await bot.send_message(message.chat.id, text='\n'.join(map(str, adm_message)))


@dp.message_handler()
async def echo(message: types.Message):

    usr_name = (message.from_user.first_name if message.from_user.first_name is not None else 'Noname')
    await message.answer(f'Привет, {str(usr_name)}!\nЗапиши голосовое сообщение удерживая микрофон для преобразования в текст')


@dp.message_handler(content_types=['voice'])
async def voice_message_handler(message: types.Message):
    # скачиваем голос в tempfile
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path

    tf = tempfile.NamedTemporaryFile(prefix="telebot_", delete=True)
    tf_filename = tf.name + '.wav'
    await bot.download_file(file_path, tf_filename)
    # конвертим
    user_voice = convert_to_pcm16b16000r(tf_filename)
    # транскрибируем
    try:
        output_text = transcri(user_voice, model_ru)
    except Exception as er:
        print(er)
        output_text = 'Сорян, что-то пошло не так'
    finally:
        os.remove(user_voice)
    await bot.send_message(message.chat.id, text=output_text)

    add_record_sql(message.from_user.id, message.date, message.date,
    output_text, message.from_user.first_name,
    message.from_user.last_name, message.from_user.username)


if __name__ == "__main__":
    # Запуск бота
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    while True:
        try:
            executor.start_polling(dp, skip_updates=True)
        except Exception as er:
            print(er)
