from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
import database
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
import uvicorn
import jwt
from datetime import datetime,timezone,timedelta

JWT_SECRET = "vehicle management system secret key" 
JWT_ALGORITHM = "HS256"

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def create_jwt_token(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_jwt_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload["user_id"]
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
    
def get_current_user_id(request: Request):
    token = request.cookies.get("auth_cookie")
    if not token:
        return None
    user_id = decode_jwt_token(token)
    return user_id

def execute_query(query, params=None, fetchone=False, fetchall=False, commit=False):
    conn = database.get_db()
    cur = conn.cursor()
    cur.execute(query, params)
    
    result = None
    if fetchone:
        result = cur.fetchone()
    elif fetchall:
        result = cur.fetchall()
    
    if commit:
        conn.commit()
    
    cur.close()
    conn.close()
    return result

app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return RedirectResponse(url="/login",status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = execute_query(
        "SELECT * FROM users WHERE username = %s AND password = %s",
        (username, password),
        fetchone=True
    )
    
    if not user:
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "error": "Invalid username or password"
        })
    token = create_jwt_token(user['id'])
    response= RedirectResponse(url=f"/user/dashboard?user_id={user['id']}" if not user['is_admin'] else "/admin/dashboard", status_code=303)
    response.set_cookie(
        key="auth_cookie",
        value=token,
        max_age=24*60*60,
        secure=False,
        samesite="Lax",
        path="/"
    )
    return response

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", {"request": request})

@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...)
):
    existing_user = execute_query(
        "SELECT * FROM users WHERE username = %s",
        (username,),
        fetchone=True
    )
    
    if existing_user:
        return templates.TemplateResponse("auth/register.html", {
            "request": request,
            "error": "Username already exists"
        })
    
    execute_query(
        """INSERT INTO users (username, email, phone, password)
           VALUES (%s, %s, %s, %s)""",
        (username, email, phone, password),
        commit=True
    )
    
    return RedirectResponse(url="/login", status_code=303)

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    user = execute_query("SELECT * FROM users WHERE id = %s", (user_id,), fetchone=True)
    if not user or not user['is_admin']:
        return RedirectResponse(url="/login")
    
    parking_lots = execute_query("SELECT * FROM parking_lots", fetchall=True)
    users = execute_query("SELECT * FROM users", fetchall=True)
    
    spot_stats = execute_query("""
        SELECT pl.id, pl.name, 
               COUNT(ps.id) as total_spots,
               SUM(CASE WHEN ps.status = 'O' THEN 1 ELSE 0 END) as occupied_spots
        FROM parking_lots pl
        LEFT JOIN parking_spots ps ON pl.id = ps.lot_id
        GROUP BY pl.id, pl.name
    """, fetchall=True)
    
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user": user,
        "parking_lots": parking_lots,
        "users": users,
        "spot_stats": spot_stats
    })

@app.get("/admin/parking-lots", response_class=HTMLResponse)
async def admin_parking_lots(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    parking_lots = execute_query("""
        SELECT pl.*, 
               COUNT(ps.id) as total_spots,
               SUM(CASE WHEN ps.status = 'O' THEN 1 ELSE 0 END) as occupied_spots
        FROM parking_lots pl
        LEFT JOIN parking_spots ps ON pl.id = ps.lot_id
        GROUP BY pl.id
    """, fetchall=True)
    
    return templates.TemplateResponse("admin/parking_lots.html", {
        "request": request,
        "parking_lots": parking_lots
    })

@app.get("/admin/create-lot", response_class=HTMLResponse)
async def create_lot_page(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("admin/create_lot.html", {"request": request})

@app.post("/admin/create-lot")
async def create_lot(
    request: Request,
    name: str = Form(...),
    price: float = Form(...),
    address: str = Form(...),
    pin_code: str = Form(...),
    max_spots: int = Form(...),
    rows: int = Form(...),
    columns: int = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...)
):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    lot = execute_query(
        """INSERT INTO parking_lots (name, price_per_hour, address, pin_code, max_spots,rows,columns,latitude,longitude)
           VALUES (%s, %s, %s, %s, %s,%s,%s,%s,%s) RETURNING id""",
        (name, price, address, pin_code, max_spots,rows,columns,latitude,longitude),
        fetchone=True,
        commit=True
    )
    
    cols=10
    for i in range(max_spots):
        row = (i // columns) + 1
        col = (i % columns) + 1
        execute_query(
            "INSERT INTO parking_spots (lot_id, spot_number, status, row, col) VALUES (%s, %s, 'A', %s, %s)",
            (lot['id'], f"SP-{i+1:03d}", row, col),
            commit=True
        )
    return RedirectResponse(url="/admin/parking-lots", status_code=303)

@app.get("/admin/user/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(request: Request, user_id: int):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse(url="/login")
    
    user = execute_query(
        "SELECT * FROM users WHERE id = %s",
        (user_id,),
        fetchone=True
    )
    if not user:
        return HTMLResponse("User not found", status_code=404)
    return templates.TemplateResponse("admin/user_detail.html", {
        "request": request,
        "user": user
    })

@app.get("/admin/edit-lot/{lot_id}", response_class=HTMLResponse)
async def edit_lot_page(request: Request, lot_id: int):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    lot = execute_query(
        "SELECT * FROM parking_lots WHERE id = %s",
        (lot_id,),
        fetchone=True
    )
    
    if not lot:
        raise HTTPException(status_code=404, detail="Parking lot not found")
    
    return templates.TemplateResponse("admin/edit_lot.html", {
        "request": request,
        "lot": lot
    })

@app.post("/admin/edit-lot/{lot_id}")
async def edit_lot(
    request: Request,
    lot_id: int,
    name: str = Form(...),
    price: float = Form(...),
    address: str = Form(...),
    pin_code: str = Form(...),
    max_spots: int = Form(...),
    rows: int = Form(...),
    columns: int = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...)
):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    execute_query(
        """UPDATE parking_lots 
           SET name = %s, price_per_hour = %s, address = %s, pin_code = %s,max_spots=%s,rows=%s,columns=%s,latitude=%s,longitude=%s, updated_at = CURRENT_TIMESTAMP
           WHERE id = %s""",
        (name, price, address, pin_code,max_spots,rows,columns,latitude,longitude, lot_id),
        commit=True
    )
    
    lot = execute_query("SELECT max_spots FROM parking_lots WHERE id = %s", (lot_id,), fetchone=True)
    max_spots = lot['max_spots']

    execute_query("DELETE FROM bills WHERE spot_id IN (SELECT id FROM parking_spots WHERE lot_id = %s)", (lot_id,), commit=True)
    execute_query("DELETE FROM reservations WHERE spot_id IN (SELECT id FROM parking_spots WHERE lot_id = %s)", (lot_id,), commit=True)
    execute_query("DELETE FROM parking_spots WHERE lot_id = %s", (lot_id,), commit=True)

    for i in range(max_spots):
        row_num = (i // columns) + 1
        col_num = (i % columns) + 1
        execute_query(
            "INSERT INTO parking_spots (lot_id, spot_number, status, row, col) VALUES (%s, %s, 'A', %s, %s)",
            (lot_id, f"SP-{i+1:03d}", row_num, col_num),
            commit=True
        )
    
    return RedirectResponse(url="/admin/parking-lots", status_code=303)

@app.get("/admin/delete-lot/{lot_id}")
async def delete_lot(lot_id: int, request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    
    occupied_spots = execute_query(
        "SELECT COUNT(*) FROM parking_spots WHERE lot_id = %s AND status = 'O'",
        (lot_id,),
        fetchone=True
    )
    
    if occupied_spots['count'] > 0:
        return RedirectResponse(url="/admin/parking-lots?error=Cannot delete lot with occupied spots", status_code=303)
    
    execute_query(
        "DELETE FROM parking_lots WHERE id = %s",
        (lot_id,),
        commit=True
    )
    return RedirectResponse(url="/admin/parking-lots", status_code=303)


@app.get("/admin/search-spot", response_class=HTMLResponse)
async def admin_search_spot(request: Request, lot_id: int, query: str):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    spot = execute_query(
        "SELECT * FROM parking_spots WHERE lot_id = %s AND spot_number = %s",
        (lot_id, query),
        fetchone=True
    )
    lot = execute_query("SELECT * FROM parking_lots WHERE id = %s", (lot_id,), fetchone=True)
    return templates.TemplateResponse("admin/search_spot.html", {
        "request": request,
        "spot": spot,
        "lot": lot,
        "query": query
    })

@app.get("/admin/parking-spots/{lot_id}", response_class=HTMLResponse)
async def admin_parking_spots(request: Request, lot_id: int):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    lot = execute_query(
        "SELECT * FROM parking_lots WHERE id = %s",
        (lot_id,),
        fetchone=True
    )
    
    spots = execute_query(
        """SELECT ps.*, u.username, r.parking_timestamp
           FROM parking_spots ps
           LEFT JOIN reservations r ON ps.id = r.spot_id AND r.leaving_timestamp IS NULL
           LEFT JOIN users u ON r.user_id = u.id
           WHERE ps.lot_id = %s
           ORDER BY ps.spot_number""",
        (lot_id,),
        fetchall=True
    )
    
    return templates.TemplateResponse("admin/parking_spots.html", {
        "request": request,
        "lot": lot,
        "parking_spots": spots
    })

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    users = execute_query("SELECT * FROM users", fetchall=True)
    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "users": users
    })

@app.get("/admin/delete-user/{user_id}")
async def delete_user(user_id: int, request: Request):
    uid = get_current_user_id(request)
    if not uid:
        return RedirectResponse(url="/login")
    active_reservations = execute_query(
        "SELECT COUNT(*) FROM reservations WHERE user_id = %s AND leaving_timestamp IS NULL",
        (user_id,),
        fetchone=True
    )
    
    if active_reservations['count'] > 0:
        return RedirectResponse(url="/admin/users?error=User has active reservations!", status_code=303)
    
    execute_query(
        "DELETE FROM users WHERE id = %s",
        (user_id,),
        commit=True
    )
    return RedirectResponse(url="/admin/users", status_code=303)

# User routes
@app.get("/user/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request, user_id: int):
    uid = get_current_user_id(request)
    if not uid or int(user_id)!=int(uid):
        return RedirectResponse(url="/login")   
    
    user = execute_query(
        "SELECT * FROM users WHERE id = %s",
        (user_id,),
        fetchone=True
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    parking_lots = execute_query(
        """SELECT pl.*, 
           (SELECT COUNT(*) FROM parking_spots ps WHERE ps.lot_id = pl.id AND ps.status = 'A') as available_spots
           FROM parking_lots pl""",
        fetchall=True
    )
    
    active_reservations = execute_query(
        """SELECT r.*, ps.spot_number, pl.name as lot_name, pl.price_per_hour
           FROM reservations r
           JOIN parking_spots ps ON r.spot_id = ps.id
           JOIN parking_lots pl ON ps.lot_id = pl.id
           WHERE r.user_id = %s AND r.leaving_timestamp IS NULL
           ORDER BY r.parking_timestamp DESC""",
        (user_id,),
        fetchall=True
    )
    
    past_reservations = execute_query(
        """SELECT r.*, ps.spot_number, pl.name as lot_name, pl.price_per_hour
           FROM reservations r
           JOIN parking_spots ps ON r.spot_id = ps.id
           JOIN parking_lots pl ON ps.lot_id = pl.id
           WHERE r.user_id = %s AND r.leaving_timestamp IS NOT NULL
           ORDER BY r.leaving_timestamp DESC
           LIMIT 5""",
        (user_id,),
        fetchall=True
    )
    
    return templates.TemplateResponse("user/dashboard.html", {
        "request": request,
        "user": user,
        "parking_lots": parking_lots,
        "active_reservations": active_reservations,
        "past_reservations": past_reservations
    })

@app.get("/user/book/{lot_id}", response_class=HTMLResponse)
async def user_book_lot(request: Request, lot_id: int, user_id: int):
    uid = get_current_user_id(request)
    if not uid or int(user_id)!=int(uid):
        return RedirectResponse(url="/login")
    
    lot = execute_query("SELECT * FROM parking_lots WHERE id = %s", (lot_id,), fetchone=True)
    spots1 = execute_query(
        "SELECT * FROM parking_spots WHERE lot_id = %s ORDER BY row, col",
        (lot_id,),
        fetchall=True
    )
    max_row = max([spot['row'] for spot in spots1]) if spots1 else 1
    max_col = max([spot['col'] for spot in spots1]) if spots1 else 1
    spots = [dict(row) for row in spots1]
    spot_map = {(s['row'], s['col']): s for s in spots}
    return templates.TemplateResponse("user/parking_lot.html", {
        "request": request,
        "lot": lot,
        "spots": spots,
        "spot_map": spot_map,
        "user_id": user_id,
        "max_row": max_row,
        "max_col": max_col
    })

@app.post("/user/reserve")
async def user_reserve(
    request: Request,
    lot_id: int = Form(...),
    user_id: int = Form(...),
    spot_id: int = Form(...)
):
    uid = get_current_user_id(request)
    if not uid or int(user_id)!=int(uid):
        return RedirectResponse(url="/login")
    spot = execute_query(
        "SELECT * FROM parking_spots WHERE id = %s AND lot_id = %s AND status = 'A'",
        (spot_id, lot_id),
        fetchone=True
    )
    if not spot:
        return HTMLResponse("Spot not available", status_code=400)
    execute_query(
        "INSERT INTO reservations (spot_id, user_id) VALUES (%s, %s)",
        (spot_id, user_id),
        commit=True
    )
    execute_query(
        "UPDATE parking_spots SET status = 'O' WHERE id = %s",
        (spot_id,),
        commit=True
    )
    return RedirectResponse(url=f"/user/dashboard?user_id={user_id}", status_code=303)

@app.get("/user/release/{reservation_id}")
async def user_release(request: Request,reservation_id: int, user_id: int):
    uid = get_current_user_id(request)
    if not uid or int(user_id)!=int(uid):
        return RedirectResponse(url="/login")
    reservation = execute_query(
        """SELECT r.*, pl.price_per_hour
           FROM reservations r
           JOIN parking_spots ps ON r.spot_id = ps.id
           JOIN parking_lots pl ON ps.lot_id = pl.id
           WHERE r.id = %s""",
        (reservation_id,),
        fetchone=True
    )
    bill=None
    if reservation:
        parking_time = datetime.now() - reservation['parking_timestamp']
        hours = max(1, parking_time.seconds // 3600)  # Minimum 1 hour
        cost = hours * reservation['price_per_hour']
        
        execute_query(
            """UPDATE reservations 
               SET leaving_timestamp = CURRENT_TIMESTAMP, 
                   cost = %s, 
                   payment_status = 'paid'
               WHERE id = %s""",
            (cost, reservation_id),
            commit=True
        )
        
        execute_query(
            "UPDATE parking_spots SET status = 'A' WHERE id = %s",
            (reservation['spot_id'],),
            commit=True
        )
        sid=reservation['spot_id']
        lot_id_row=execute_query("SELECT lot_id FROM parking_spots WHERE id = %s",(sid,),fetchone=True)
        lot_id=lot_id_row['lot_id'] if lot_id_row else None

        execute_query(
        """INSERT INTO bills (reservation_id, user_id, spot_id, start_time, end_time, hours, rate, cost,lot_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s,%s)""",
        (
            reservation_id,
            reservation['user_id'],
            reservation['spot_id'],
            reservation['parking_timestamp'],
            datetime.now(),
            hours,
            reservation['price_per_hour'],
            cost,
            lot_id
        ),
        commit=True
        )
        bill = execute_query(
            "SELECT * FROM bills WHERE reservation_id = %s ORDER BY id DESC LIMIT 1",
            (reservation_id,),
            fetchone=True
        )
    
    user = execute_query(
        "SELECT * FROM users WHERE id = %s",
        (user_id,),
        fetchone=True
    )
    
    return templates.TemplateResponse("user/bill.html", {
        "request": request,
        "user": user,
        "bill": bill,
        "reservation": reservation
    })


@app.get("/user/bills", response_class=HTMLResponse)
async def user_bills(request: Request, user_id: int):
    uid = get_current_user_id(request)
    if not user_id or int(user_id)!=int(uid):
        return RedirectResponse(url="/login")
    bills = execute_query(
        """SELECT b.*, r.spot_id, r.parking_timestamp, r.leaving_timestamp
           FROM bills b
           JOIN reservations r ON b.reservation_id = r.id
           WHERE b.user_id = %s
           ORDER BY b.created_at DESC""",
        (user_id,),
        fetchall=True
    )
    return templates.TemplateResponse("user/bills.html", {
        "request": request,
        "bills": bills
    })


@app.get("/user/history", response_class=HTMLResponse)
async def user_history(request: Request, user_id: int):
    uid = get_current_user_id(request)
    if not user_id or int(user_id)!=int(uid):
        return RedirectResponse(url="/login")
    user = execute_query(
        "SELECT * FROM users WHERE id = %s",
        (user_id,),
        fetchone=True
    )
    
    reservations = execute_query(
        """SELECT r.*, ps.spot_number, pl.name as lot_name, pl.price_per_hour
           FROM reservations r
           JOIN parking_spots ps ON r.spot_id = ps.id
           JOIN parking_lots pl ON ps.lot_id = pl.id
           WHERE r.user_id = %s
           ORDER BY r.leaving_timestamp DESC""",
        (user_id,),
        fetchall=True
    )
    
    return templates.TemplateResponse("user/history.html", {
        "request": request,
        "user": user,
        "reservations": reservations
    })

@app.middleware("https")
async def no_middlewarecache(request: Request, call_next):
    response=await call_next(request)
    if request.url.path.startswith("/user") or request.url.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login")
    response.delete_cookie("auth_cookie",path="/",secure=False,samesite="Lax")
    return response

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)