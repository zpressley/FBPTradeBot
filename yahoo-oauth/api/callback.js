module.exports = (req, res) => {
    const code = req.query.code;
    res.send(`Authorization Code: ${code}`);
};
 